from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from svmap.agents import AgentRegistry
from svmap.models import (
    ConstraintResult,
    ExecutionContext,
    ExecutionReport,
    NodeExecutionRecord,
    NodeFailure,
    RuntimeBudget,
    TaskNode,
    TaskTree,
)
from svmap.verification import VerificationResult, VerifierEngine

from .replanner import BaseReplanner
from .trace import TraceLogger


class ExecutionRuntime:
    def __init__(
        self,
        registry: AgentRegistry,
        verifier_engine: VerifierEngine,
        replanner: Optional[BaseReplanner] = None,
        trace_logger: Optional[TraceLogger] = None,
        stop_on_failure: bool = True,
        parallel: bool = False,
        max_runtime_steps: int = 200,
        budget: Optional[RuntimeBudget] = None,
    ) -> None:
        self.registry = registry
        self.verifier_engine = verifier_engine
        self.replanner = replanner
        self.trace_logger = trace_logger
        self.stop_on_failure = stop_on_failure
        self.parallel = parallel
        self.max_runtime_steps = max_runtime_steps
        self.budget = budget or RuntimeBudget(max_runtime_steps=max_runtime_steps)

    def ensure_node_assignment(self, node: TaskNode) -> None:
        if node.assigned_agent:
            return
        candidates = self.registry.rank_candidates(node)
        if not candidates:
            return
        node.assigned_agent = candidates[0].name
        node.fallback_agents = [spec.name for spec in candidates[1:]]

    def collect_node_inputs(self, node: TaskNode, context: ExecutionContext) -> Dict[str, Any]:
        dependency_outputs = {dep: context.node_outputs[dep] for dep in node.dependencies if dep in context.node_outputs}
        return {
            "node_inputs": node.inputs,
            "dependency_outputs": dependency_outputs,
            "global_context": context.global_context,
        }

    def execute_final_response_node(
        self,
        node: TaskNode,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> NodeExecutionRecord:
        return self.execute_node(node=node, tree=tree, context=context)

    def _merge_verification_results(self, scope_results: List[VerificationResult]) -> VerificationResult:
        details: List[ConstraintResult] = []
        reasons: List[str] = []
        repair_hints: List[str] = []
        violation_scopes: List[str] = []
        failure_type = ""
        fatal = False
        for result in scope_results:
            details.extend(result.details)
            reasons.extend(result.reasons)
            repair_hints.extend(result.repair_hints)
            violation_scopes.extend(result.violation_scopes)
            if not failure_type and result.failure_type:
                failure_type = result.failure_type
            fatal = fatal or result.fatal
        passed = all(result.passed for result in scope_results)
        return VerificationResult(
            passed=passed,
            reasons=reasons,
            details=details,
            failure_type=failure_type,
            repair_hints=sorted(set(repair_hints)),
            violation_scopes=sorted(set(violation_scopes)),
            fatal=fatal,
            confidence=1.0 if passed else 0.0,
        )

    def _run_scoped_verification(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        tree: TaskTree,
        verify_context: Dict[str, Any],
    ) -> VerificationResult:
        scoped: List[VerificationResult] = []
        node_result = self.verifier_engine.verify_node(node=node, output=output, context=verify_context)
        scoped.append(node_result)
        if not node_result.passed:
            return self._merge_verification_results(scoped)

        for dep_id in node.dependencies:
            src = tree.nodes.get(dep_id)
            if src is None:
                continue
            edge_result = self.verifier_engine.verify_edge(
                src_node=src,
                dst_node=node,
                dst_output=output,
                context=verify_context,
            )
            scoped.append(edge_result)

        if all(result.passed for result in scoped) and node.is_final_response():
            scoped.append(
                self.verifier_engine.verify_subtree(
                    tree=tree,
                    root_node_id=node.id,
                    context=verify_context,
                )
            )
            scoped.append(
                self.verifier_engine.verify_global(
                    tree=tree,
                    context=verify_context,
                )
            )
        return self._merge_verification_results(scoped)

    def execute_node(
        self,
        node: TaskNode,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> NodeExecutionRecord:
        retries = 0
        attempts = 0
        retry_feedback: List[str] = []
        verification_results: List[ConstraintResult] = []
        output: Dict[str, Any] = {}
        candidate_agents = node.candidate_agents()
        active_agent = node.assigned_agent or ""
        if not active_agent:
            return NodeExecutionRecord(
                node_id=node.id,
                status="failed",
                attempts=0,
                agent_used="",
                candidate_agents=candidate_agents,
                verify_errors=["no_assigned_agent"],
                verification_results=[],
                task_type=node.spec.task_type,
                output_mode=node.spec.output_mode,
                answer_role=node.spec.answer_role,
                failure_type="runtime_assignment_error",
                fatal=True,
            )

        record = NodeExecutionRecord(
            node_id=node.id,
            status="running",
            attempts=0,
            agent_used=active_agent,
            candidate_agents=candidate_agents,
            start_ts=time.time(),
            task_type=node.spec.task_type,
            output_mode=node.spec.output_mode,
            answer_role=node.spec.answer_role,
        )

        while retries <= node.max_retry:
            attempts += 1
            node_inputs = self.collect_node_inputs(node, context)
            record.input_snapshot = node_inputs

            agent = self.registry.get(active_agent)
            output = agent.run(
                node=node,
                inputs=node_inputs,
                context={"attempt": attempts, "retry_feedback": retry_feedback},
            )
            verify_context = dict(node_inputs)
            verify_context["task_tree"] = tree
            verify_context["total_attempts"] = int(context.shared_memory.get("total_attempts", 0))
            verify_context["total_replans"] = int(context.shared_memory.get("total_replans", 0))
            verify_result = self._run_scoped_verification(
                node=node,
                output=output,
                tree=tree,
                verify_context=verify_context,
            )
            verification_results.extend(verify_result.details)

            if verify_result.passed:
                node.outputs = output
                node.status = "success"
                context.node_outputs[node.id] = output
                record.status = "success"
                record.attempts = attempts
                record.agent_used = active_agent
                record.output = output
                record.verification_results = verification_results
                record.end_ts = time.time()
                record.intent_status = node.intent_status
                record.graph_version = tree.version
                record.failure_type = ""
                record.repair_hint = ""
                record.fatal = False
                return record

            retries += 1
            retry_feedback = verify_result.reasons
            record.failure_type = verify_result.failure_type or self.infer_failure_type(verify_result.details)
            record.repair_hint = verify_result.repair_hints[0] if verify_result.repair_hints else ""
            record.fatal = verify_result.fatal
            if retries <= node.max_retry and retries >= 2 and node.fallback_agents:
                idx = min(retries - 2, len(node.fallback_agents) - 1)
                active_agent = node.fallback_agents[idx]

        node.status = "failed"
        record.status = "failed"
        record.attempts = attempts
        record.agent_used = active_agent
        record.output = output
        record.verify_errors = retry_feedback
        record.verification_results = verification_results
        record.end_ts = time.time()
        record.intent_status = node.intent_status
        record.graph_version = tree.version
        record.task_type = node.spec.task_type
        record.output_mode = node.spec.output_mode
        record.answer_role = node.spec.answer_role
        if not record.failure_type:
            record.failure_type = self.infer_failure_type(record.verification_results)
        if not record.repair_hint:
            repair_hints = [x.repair_hint for x in record.verification_results if x.repair_hint]
            record.repair_hint = repair_hints[0] if repair_hints else ""
        if not record.fatal:
            record.fatal = record.failure_type in {
                "internal_execution_error",
                "final_answer_missing_structure",
                "final_query_echo",
                "echo_retrieval",
                "empty_extraction",
                "intent_misalignment",
                "grounding_error",
                "final_output_not_valid",
            }
        return record

    def handle_failure(
        self,
        node: TaskNode,
        failure: NodeFailure,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> tuple[int, bool, Dict[str, Any]]:
        if self.replanner is None:
            tree.mark_skipped_subtree(node.id)
            return 0, False, {"chosen_action": "abort", "graph_delta": {}, "saved_downstream_nodes": 0}
        saved = self.compute_saved_downstream_nodes(node.id, tree)
        decision = self.replanner.decide(node=node, failure=failure, tree=tree, context=context)
        before_version = tree.version
        self.replanner.apply(decision=decision, tree=tree, context=context)
        after_version = tree.version
        patch_template = ""
        if decision.patch and isinstance(decision.patch, dict):
            patch_template = str(decision.patch.get("template", ""))
        meta = {
            "chosen_action": decision.action,
            "graph_delta": {
                "before_version": before_version,
                "after_version": after_version,
            },
            "saved_downstream_nodes": saved,
            "failure_type": decision.failure_type or failure.failure_type,
            "patch_template": patch_template,
            "affected_nodes": [node.id, *tree.get_downstream_nodes(node.id)],
        }
        if self.trace_logger:
            self.trace_logger.log_event(
                "replan_decision",
                {"node_id": node.id, "action": decision.action, "reason": decision.reason},
            )
            self.trace_logger.log_graph_delta(
                before_version=before_version,
                after_version=after_version,
                payload={"node_id": node.id, "action": decision.action, "saved_downstream_nodes": saved},
            )
        recovered = decision.action in {"retry_same", "switch_agent", "patch_subgraph"}
        if decision.action in {"replan_subtree", "replan_global"}:
            recovered = True
        return 1, recovered, meta

    def execute_ready_batch(
        self,
        ready_nodes: List[TaskNode],
        tree: TaskTree,
        context: ExecutionContext,
    ) -> List[NodeExecutionRecord]:
        records: List[NodeExecutionRecord] = []
        for node in ready_nodes:
            self.ensure_node_assignment(node)
            if self.trace_logger:
                self.trace_logger.log_event("node_start", {"node_id": node.id})
            node.status = "running"
            if node.is_final_response():
                record = self.execute_final_response_node(node=node, tree=tree, context=context)
            else:
                record = self.execute_node(node=node, tree=tree, context=context)
            if self.trace_logger:
                self.trace_logger.log_event(
                    "node_end",
                    {
                        "node_id": node.id,
                        "status": record.status,
                        "attempts": record.attempts,
                        "agent": record.agent_used,
                    },
                )
            records.append(record)
        return records

    def infer_failure_type(self, verification_results: List[Any]) -> str:
        failed = [item for item in verification_results if not item.passed]
        if not failed:
            return "unknown"
        counts: Dict[str, int] = {}
        for item in failed:
            failure_type = str(getattr(item, "failure_type", "") or "")
            if not failure_type:
                code = str(getattr(item, "code", ""))
                if "final_placeholder_output" in code:
                    failure_type = "final_placeholder_output"
                elif "final_topic_drift" in code:
                    failure_type = "final_topic_drift"
                elif "plan_topic_drift" in code:
                    failure_type = "plan_topic_drift"
                elif "plan_coverage" in code or "coverage_" in code:
                    failure_type = "plan_coverage_incomplete"
                elif "requirements_" in code:
                    failure_type = "requirements_analysis_failed"
                elif "schema_day_template" in code or "schema_progression" in code or "schema_topic" in code:
                    failure_type = "schema_design_failed"
                elif "low_information_output" in code or "placeholder" in code:
                    failure_type = "low_information_output"
                elif "schema" in code or "type" in code or "required" in code:
                    failure_type = "schema_error"
                elif "intent" in code:
                    failure_type = "intent_misalignment"
                elif "internal_execution_error" in code or "runtime_error" in code:
                    failure_type = "internal_execution_error"
                elif "final_answer_missing_structure" in code:
                    failure_type = "final_answer_missing_structure"
                elif "final_answer_query_echo" in code:
                    failure_type = "final_query_echo"
                elif "echo_retrieval" in code:
                    failure_type = "echo_retrieval"
                elif "empty_extraction" in code:
                    failure_type = "empty_extraction"
                elif "ground" in code:
                    failure_type = "grounding_error"
                elif "consistency" in code or "cross_node" in code:
                    failure_type = "consistency_error"
                elif "evidence" in code or "source" in code or "semantic" in code:
                    failure_type = "evidence_error"
                else:
                    failure_type = "rule"
            counts[failure_type] = counts.get(failure_type, 0) + 1
        return sorted(counts.items(), key=lambda item: item[1], reverse=True)[0][0]

    def should_abort_for_budget(
        self,
        report: ExecutionReport,
        budget: RuntimeBudget,
        runtime_steps: int,
    ) -> bool:
        total_attempts = sum(r.attempts for r in report.node_records.values())
        if runtime_steps >= budget.max_runtime_steps:
            return True
        if total_attempts >= budget.max_total_attempts:
            return True
        if report.replan_count >= budget.max_total_replans:
            return True
        return False

    def compute_saved_downstream_nodes(
        self,
        failed_node_id: str,
        tree: TaskTree,
    ) -> int:
        downstream = tree.get_downstream_nodes(failed_node_id)
        count = 0
        for node_id in downstream:
            node = tree.nodes.get(node_id)
            if node is not None and node.status == "pending":
                count += 1
        return count

    def _avg_saved(self, structural_savings: Dict[str, Any]) -> float:
        saved = structural_savings.get("saved_downstream_nodes", [])
        if not isinstance(saved, list):
            return 0.0
        return float(sum(saved) / max(len(saved), 1))

    def _build_report(
        self,
        *,
        success: bool,
        node_records: Dict[str, NodeExecutionRecord],
        total_retries: int,
        verification_failures: int,
        replan_count: int,
        tree: TaskTree,
        final_node_id: Optional[str],
        final_output: Optional[Dict[str, Any]],
        replan_actions: List[str],
        structural_savings: Dict[str, Any],
        budget_exhausted: bool,
        error: str,
        stalled_node_ids: List[str],
        failure_summary: Dict[str, int],
    ) -> ExecutionReport:
        return ExecutionReport(
            success=success,
            node_records=node_records,
            total_retries=total_retries,
            verification_failures=verification_failures,
            replan_count=replan_count,
            plan_versions=tree.version,
            budget_exhausted=budget_exhausted,
            replan_actions=replan_actions,
            structural_savings={
                "avg_saved_downstream_nodes": self._avg_saved(structural_savings),
                "parallelizable_node_ratio": 0.0,
                "avg_cost_saved_vs_full_rerun": 0.0,
            },
            final_node_id=final_node_id,
            final_output=final_output,
            node_task_types={nid: node.spec.task_type for nid, node in tree.nodes.items()},
            task_family=str(tree.metadata.get("task_family", "")),
            error=error,
            stalled_node_ids=stalled_node_ids,
            failure_summary=failure_summary,
        )

    def execute(self, tree: TaskTree, context: ExecutionContext) -> ExecutionReport:
        tree.ensure_single_final_response()
        tree.validate()
        sink_nodes = tree.get_sink_nodes()
        if not sink_nodes:
            return self._build_report(
                success=False,
                node_records={},
                total_retries=0,
                verification_failures=0,
                replan_count=0,
                tree=tree,
                final_node_id=None,
                final_output=None,
                replan_actions=[],
                structural_savings={"saved_downstream_nodes": []},
                budget_exhausted=False,
                error="no_final_response_node",
                stalled_node_ids=[],
                failure_summary={"no_final_response_node": 1},
            )
        if len(sink_nodes) > 1:
            return self._build_report(
                success=False,
                node_records={},
                total_retries=0,
                verification_failures=0,
                replan_count=0,
                tree=tree,
                final_node_id=None,
                final_output=None,
                replan_actions=[],
                structural_savings={"saved_downstream_nodes": []},
                budget_exhausted=False,
                error="multiple_sink_nodes",
                stalled_node_ids=[],
                failure_summary={"multiple_sink_nodes": 1},
            )

        final_node_id = sink_nodes[0]
        final_sink_node = tree.nodes.get(final_node_id)
        if final_sink_node is None or not final_sink_node.is_final_response():
            return self._build_report(
                success=False,
                node_records={},
                total_retries=0,
                verification_failures=0,
                replan_count=0,
                tree=tree,
                final_node_id=final_node_id,
                final_output=None,
                replan_actions=[],
                structural_savings={"saved_downstream_nodes": []},
                budget_exhausted=False,
                error="sink_not_final_response",
                stalled_node_ids=[],
                failure_summary={"sink_not_final_response": 1},
            )

        node_records: Dict[str, NodeExecutionRecord] = {}
        total_retries = 0
        verification_failures = 0
        replan_count = 0
        replan_actions: List[str] = []
        structural_savings: Dict[str, Any] = {"saved_downstream_nodes": []}
        budget_exhausted = False
        runtime_steps = 0
        stalled_node_ids: List[str] = []
        failure_summary: Dict[str, int] = {}

        if self.trace_logger:
            self.trace_logger.log_event(
                "plan_generated",
                {"node_count": len(tree.nodes), "version": tree.version},
            )

        while runtime_steps < self.max_runtime_steps:
            runtime_steps += 1
            ready_nodes = tree.get_ready_nodes()
            if not ready_nodes:
                pending_nodes = [n for n in tree.nodes.values() if n.status == "pending"]
                if pending_nodes:
                    stalled_node_ids = [n.id for n in pending_nodes]
                    failure_summary["graph_stalled"] = failure_summary.get("graph_stalled", 0) + 1
                    for node in pending_nodes:
                        node.status = "failed"
                break

            batch_records = self.execute_ready_batch(
                ready_nodes=ready_nodes,
                tree=tree,
                context=context,
            )
            for record in batch_records:
                node = tree.nodes.get(record.node_id)
                if node is None:
                    continue
                node_records[node.id] = record
                total_retries += max(record.attempts - 1, 0)
                context.shared_memory["total_attempts"] = int(
                    context.shared_memory.get("total_attempts", 0)
                ) + record.attempts
                verification_failures += sum(
                    1
                    for item in record.verification_results
                    if not item.passed and item.severity == "error"
                )

                if record.status != "success":
                    failure_type = record.failure_type or self.infer_failure_type(record.verification_results)
                    failure_summary[failure_type] = failure_summary.get(failure_type, 0) + 1
                    if self.trace_logger:
                        self.trace_logger.log_constraint_violation(
                            node_id=node.id,
                            failure_type=failure_type,
                            reasons=record.verify_errors,
                        )
                        if failure_type in {
                            "final_placeholder_output",
                            "plan_coverage_incomplete",
                            "plan_topic_drift",
                            "final_topic_drift",
                            "requirements_analysis_failed",
                            "schema_design_failed",
                            "low_information_output",
                        }:
                            self.trace_logger.log_plan_quality_failure(
                                node_id=node.id,
                                failure_type=failure_type,
                                reasons=record.verify_errors,
                            )
                    failure = NodeFailure(
                        node_id=node.id,
                        failure_type=failure_type,
                        reasons=record.verify_errors,
                        output_snapshot=record.output,
                        retryable=node.execution_policy.retryable,
                        constraint_failures=[x for x in record.verification_results if not x.passed],
                        repair_hints=[x.repair_hint for x in record.verification_results if x.repair_hint],
                        violation_scopes=[x.violation_scope for x in record.verification_results if not x.passed],
                    )
                    added_replan, recovered, replan_meta = self.handle_failure(
                        node=node,
                        failure=failure,
                        tree=tree,
                        context=context,
                    )
                    replan_count += added_replan
                    context.shared_memory["total_replans"] = int(
                        context.shared_memory.get("total_replans", 0)
                    ) + added_replan
                    replan_actions.append(replan_meta.get("chosen_action", ""))
                    structural_savings["saved_downstream_nodes"].append(
                        replan_meta.get("saved_downstream_nodes", 0)
                    )
                    record.saved_downstream_nodes = int(replan_meta.get("saved_downstream_nodes", 0))
                    record.replan_action = str(replan_meta.get("chosen_action", ""))

                    if self.stop_on_failure and not recovered:
                        failure_summary["replan_failure"] = failure_summary.get("replan_failure", 0) + 1
                        return self._build_report(
                            success=False,
                            node_records=node_records,
                            total_retries=total_retries,
                            verification_failures=verification_failures,
                            replan_count=replan_count,
                            tree=tree,
                            final_node_id=final_node_id,
                            final_output=None,
                            replan_actions=replan_actions,
                            structural_savings=structural_savings,
                            budget_exhausted=budget_exhausted,
                            error="final_output_not_valid",
                            stalled_node_ids=stalled_node_ids,
                            failure_summary=failure_summary,
                        )

            probe_report = ExecutionReport(
                success=False,
                node_records=node_records,
                total_retries=total_retries,
                verification_failures=verification_failures,
                replan_count=replan_count,
            )
            if self.should_abort_for_budget(probe_report, self.budget, runtime_steps):
                budget_exhausted = True
                failure_summary["budget_exhausted"] = failure_summary.get("budget_exhausted", 0) + 1
                break

        final_node = tree.nodes.get(final_node_id) if final_node_id else None
        final_node_success = final_node is not None and final_node.status == "success"
        if not final_node_success:
            failure_summary["final_output_not_valid"] = failure_summary.get("final_output_not_valid", 0) + 1
            return self._build_report(
                success=False,
                node_records=node_records,
                total_retries=total_retries,
                verification_failures=verification_failures,
                replan_count=replan_count,
                tree=tree,
                final_node_id=final_node_id,
                final_output=None,
                replan_actions=replan_actions,
                structural_savings=structural_savings,
                budget_exhausted=budget_exhausted,
                error="final_output_not_valid",
                stalled_node_ids=stalled_node_ids,
                failure_summary=failure_summary,
            )

        final_record = node_records.get(final_node_id)
        if final_record is not None and final_record.fatal:
            failure_summary["final_output_not_valid"] = failure_summary.get("final_output_not_valid", 0) + 1
            return self._build_report(
                success=False,
                node_records=node_records,
                total_retries=total_retries,
                verification_failures=verification_failures,
                replan_count=replan_count,
                tree=tree,
                final_node_id=final_node_id,
                final_output=None,
                replan_actions=replan_actions,
                structural_savings=structural_savings,
                budget_exhausted=budget_exhausted,
                error="final_output_not_valid",
                stalled_node_ids=stalled_node_ids,
                failure_summary=failure_summary,
            )

        final_output = final_node.outputs or context.node_outputs.get(final_node_id) or {}
        if not isinstance(final_output, dict):
            final_output = {"answer": str(final_output)}
        final_answer = final_output.get("answer") or final_output.get("final_response")
        if not isinstance(final_answer, str) or not final_answer.strip():
            failure_summary["final_output_not_valid"] = failure_summary.get("final_output_not_valid", 0) + 1
            return self._build_report(
                success=False,
                node_records=node_records,
                total_retries=total_retries,
                verification_failures=verification_failures,
                replan_count=replan_count,
                tree=tree,
                final_node_id=final_node_id,
                final_output=None,
                replan_actions=replan_actions,
                structural_savings=structural_savings,
                budget_exhausted=budget_exhausted,
                error="final_output_not_valid",
                stalled_node_ids=stalled_node_ids,
                failure_summary=failure_summary,
            )

        success = all(n.status in {"success", "skipped"} for n in tree.nodes.values()) and all(
            n.status != "failed" for n in tree.nodes.values()
        )
        return self._build_report(
            success=success,
            node_records=node_records,
            total_retries=total_retries,
            verification_failures=verification_failures,
            replan_count=replan_count,
            tree=tree,
            final_node_id=final_node_id,
            final_output=final_output,
            replan_actions=replan_actions,
            structural_savings=structural_savings,
            budget_exhausted=budget_exhausted,
            error="",
            stalled_node_ids=stalled_node_ids,
            failure_summary=failure_summary,
        )
