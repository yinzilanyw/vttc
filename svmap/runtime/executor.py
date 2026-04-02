from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from svmap.agents import AgentRegistry
from svmap.models import (
    ExecutionContext,
    ExecutionReport,
    NodeExecutionRecord,
    NodeFailure,
    RuntimeBudget,
    TaskNode,
    TaskTree,
)
from svmap.verification import VerifierEngine

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

    def finalize_response(self, tree: TaskTree, context: ExecutionContext) -> Dict[str, Any]:
        final_nodes = [node for node in tree.nodes.values() if node.is_final_response()]
        if not final_nodes:
            return {"answer": "", "reason": "missing_final_node"}
        final_node = final_nodes[0]
        output = context.node_outputs.get(final_node.id, {})
        if isinstance(output, dict):
            return output
        return {"answer": str(output)}

    def execute_final_response_node(
        self,
        node: TaskNode,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> NodeExecutionRecord:
        return self.execute_node(node=node, tree=tree, context=context)

    def execute_node(
        self,
        node: TaskNode,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> NodeExecutionRecord:
        retries = 0
        attempts = 0
        retry_feedback: List[str] = []
        verification_results = []
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
            verify_result = self.verifier_engine.verify_node(node, output, verify_context)
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
                return record

            retries += 1
            retry_feedback = verify_result.reasons
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
        meta = {
            "chosen_action": decision.action,
            "graph_delta": {
                "before_version": before_version,
                "after_version": after_version,
            },
            "saved_downstream_nodes": saved,
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

    def execute(self, tree: TaskTree, context: ExecutionContext) -> ExecutionReport:
        tree.ensure_single_final_response()
        tree.validate()
        node_records: Dict[str, NodeExecutionRecord] = {}
        total_retries = 0
        verification_failures = 0
        replan_count = 0
        replan_actions: List[str] = []
        structural_savings: Dict[str, Any] = {"saved_downstream_nodes": []}
        budget_exhausted = False
        max_runtime_steps = self.max_runtime_steps
        runtime_steps = 0
        final_node_id = next((n.id for n in tree.nodes.values() if n.is_final_response()), None)

        if self.trace_logger:
            self.trace_logger.log_event(
                "plan_generated",
                {"node_count": len(tree.nodes), "version": tree.version},
            )

        while runtime_steps < max_runtime_steps:
            runtime_steps += 1
            ready_nodes = tree.get_ready_nodes()
            if not ready_nodes:
                pending_nodes = [n for n in tree.nodes.values() if n.status == "pending"]
                if pending_nodes:
                    # Stalled graph: no ready nodes while pending exists.
                    for node in pending_nodes:
                        node.status = "failed"
                    break
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
                    failure = NodeFailure(
                        node_id=node.id,
                        failure_type="verification_failed",
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
                        return ExecutionReport(
                            success=False,
                            node_records=node_records,
                            total_retries=total_retries,
                            verification_failures=verification_failures,
                            replan_count=replan_count,
                            plan_versions=tree.version,
                            budget_exhausted=budget_exhausted,
                            replan_actions=replan_actions,
                            structural_savings={
                                "avg_saved_downstream_nodes": (
                                    sum(structural_savings["saved_downstream_nodes"])
                                    / max(len(structural_savings["saved_downstream_nodes"]), 1)
                                )
                            },
                            final_node_id=final_node_id,
                            final_output=self.finalize_response(tree=tree, context=context),
                            node_task_types={nid: node.spec.task_type for nid, node in tree.nodes.items()},
                            task_family=str(tree.metadata.get("task_family", "")),
                        )

            # Continue loop so patched/reassigned nodes can be retried dynamically.
            probe_report = ExecutionReport(
                success=False,
                node_records=node_records,
                total_retries=total_retries,
                verification_failures=verification_failures,
                replan_count=replan_count,
                plan_versions=tree.version,
                replan_actions=replan_actions,
            )
            if self.should_abort_for_budget(probe_report, self.budget, runtime_steps):
                budget_exhausted = True
                break

        final_output = self.finalize_response(tree=tree, context=context)
        final_node = tree.nodes.get(final_node_id) if final_node_id else None
        final_node_success = final_node is not None and final_node.status == "success"
        success = all(n.status in {"success", "skipped"} for n in tree.nodes.values()) and all(
            n.status != "failed" for n in tree.nodes.values()
        ) and final_node_success
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
                "avg_saved_downstream_nodes": (
                    sum(structural_savings["saved_downstream_nodes"])
                    / max(len(structural_savings["saved_downstream_nodes"]), 1)
                ),
                "parallelizable_node_ratio": 0.0,
                "avg_cost_saved_vs_full_rerun": 0.0,
            },
            final_node_id=final_node_id,
            final_output=final_output,
            node_task_types={nid: node.spec.task_type for nid, node in tree.nodes.items()},
            task_family=str(tree.metadata.get("task_family", "")),
        )
