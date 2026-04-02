from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from svmap.agents import AgentRegistry
from svmap.models import (
    ExecutionContext,
    ExecutionReport,
    NodeExecutionRecord,
    NodeFailure,
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
    ) -> None:
        self.registry = registry
        self.verifier_engine = verifier_engine
        self.replanner = replanner
        self.trace_logger = trace_logger
        self.stop_on_failure = stop_on_failure

    def ensure_node_assignment(self, node: TaskNode) -> None:
        if node.assigned_agent:
            return
        candidates = self.registry.find_candidates(node.spec.capability_tag)
        if not candidates:
            return
        ranked = sorted(
            candidates,
            key=lambda spec: spec.reliability / max(spec.cost_weight * spec.latency_weight, 1e-6),
            reverse=True,
        )
        node.assigned_agent = ranked[0].name
        node.fallback_agents = [spec.name for spec in ranked[1:]]

    def collect_node_inputs(self, node: TaskNode, context: ExecutionContext) -> Dict[str, Any]:
        dependency_outputs = {dep: context.node_outputs[dep] for dep in node.dependencies if dep in context.node_outputs}
        return {
            "node_inputs": node.inputs,
            "dependency_outputs": dependency_outputs,
            "global_context": context.global_context,
        }

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
            )

        record = NodeExecutionRecord(
            node_id=node.id,
            status="running",
            attempts=0,
            agent_used=active_agent,
            candidate_agents=candidate_agents,
            start_ts=time.time(),
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
            verify_result = self.verifier_engine.verify(node, output, node_inputs)
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
        return record

    def handle_failure(
        self,
        node: TaskNode,
        failure: NodeFailure,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> tuple[int, bool]:
        if self.replanner is None:
            tree.mark_skipped_subtree(node.id)
            return 0, False
        decision = self.replanner.decide(node=node, failure=failure, tree=tree, context=context)
        self.replanner.apply(decision=decision, tree=tree, context=context)
        if self.trace_logger:
            self.trace_logger.log_event(
                "replan_decision",
                {"node_id": node.id, "action": decision.action, "reason": decision.reason},
            )
        recovered = decision.action in {"retry_same", "switch_agent", "patch_subgraph"}
        return 1, recovered

    def execute(self, tree: TaskTree, context: ExecutionContext) -> ExecutionReport:
        node_records: Dict[str, NodeExecutionRecord] = {}
        total_retries = 0
        verification_failures = 0
        replan_count = 0
        max_runtime_steps = 200
        runtime_steps = 0

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

            for node in ready_nodes:
                self.ensure_node_assignment(node)

                if self.trace_logger:
                    self.trace_logger.log_event("node_start", {"node_id": node.id})
                node.status = "running"
                record = self.execute_node(node=node, tree=tree, context=context)
                node_records[node.id] = record
                total_retries += max(record.attempts - 1, 0)
                verification_failures += sum(
                    1
                    for item in record.verification_results
                    if not item.passed and item.severity == "error"
                )

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

                if record.status != "success":
                    failure = NodeFailure(
                        node_id=node.id,
                        failure_type="verification_failed",
                        reasons=record.verify_errors,
                        output_snapshot=record.output,
                        retryable=node.execution_policy.retryable,
                    )
                    added_replan, recovered = self.handle_failure(
                        node=node,
                        failure=failure,
                        tree=tree,
                        context=context,
                    )
                    replan_count += added_replan

                    if self.stop_on_failure and not recovered:
                        return ExecutionReport(
                            success=False,
                            node_records=node_records,
                            total_retries=total_retries,
                            verification_failures=verification_failures,
                            replan_count=replan_count,
                            plan_versions=tree.version,
                        )

            # Continue loop so patched/reassigned nodes can be retried dynamically.

        success = all(n.status in {"success", "skipped"} for n in tree.nodes.values()) and all(
            n.status != "failed" for n in tree.nodes.values()
        )
        return ExecutionReport(
            success=success,
            node_records=node_records,
            total_retries=total_retries,
            verification_failures=verification_failures,
            replan_count=replan_count,
            plan_versions=tree.version,
        )
