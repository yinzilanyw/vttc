from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from svmap.models import (
    ConstraintParser,
    ExecutionContext,
    ExecutionPolicy,
    FieldSpec,
    IntentSpec,
    NodeIO,
    NodeFailure,
    NodeSpec,
    TaskNode,
    TaskTree,
)
from svmap.planning import BasePlanner, PlanningContext

from .patch_library import (
    build_calculation_patch,
    build_clarification_patch,
    build_compare_patch,
    build_crosscheck_patch,
    build_decomposition_patch,
    build_evidence_patch,
    build_final_response_patch,
    build_metric_patch as build_metric_patch_template,
    build_normalization_patch,
    build_schema_patch,
    build_summary_patch,
)


@dataclass
class ReplanDecision:
    action: str
    target_node_id: str
    patch: Optional[Dict[str, Any]] = None
    reason: str = ""
    failure_type: str = ""


@dataclass
class ReplanCandidate:
    action: str
    estimated_cost: float
    estimated_latency: float
    estimated_success_gain: float
    reason: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


class ReplanScorer:
    def score(self, candidate: ReplanCandidate, context: Dict[str, Any]) -> float:
        gain = candidate.estimated_success_gain
        cost = max(candidate.estimated_cost, 1e-6)
        latency = max(candidate.estimated_latency, 1e-6)
        return gain / (cost * latency)


class BaseReplanner(ABC):
    @abstractmethod
    def enumerate_candidates(
        self,
        node: TaskNode,
        failure: NodeFailure,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> List[ReplanCandidate]:
        raise NotImplementedError

    @abstractmethod
    def decide(
        self,
        node: TaskNode,
        failure: NodeFailure,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> ReplanDecision:
        raise NotImplementedError

    @abstractmethod
    def apply(
        self,
        decision: ReplanDecision,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> TaskTree:
        raise NotImplementedError


class ConstraintAwareReplanner(BaseReplanner):
    FAILURE_TO_ACTION: Dict[str, str] = {
        "requirements_analysis_failed": "replan_subtree",
        "schema_design_failed": "patch_subgraph",
        "generic_deliverable": "patch_subgraph",
        "non_actionable_metric": "patch_subgraph",
        "repo_binding_weak": "replan_subtree",
        "low_information_output": "replan_subtree",
    }

    def __init__(
        self,
        planner: Optional[BasePlanner] = None,
        scorer: Optional[ReplanScorer] = None,
    ) -> None:
        self.planner = planner
        self.scorer = scorer or ReplanScorer()

    def replan_for_missing_final_response(self, node: TaskNode) -> ReplanCandidate:
        return ReplanCandidate(
            action="patch_subgraph",
            estimated_cost=0.5,
            estimated_latency=0.4,
            estimated_success_gain=0.92,
            reason="missing final response grounding",
            payload=build_final_response_patch(node.id),
        )

    def replan_for_incomplete_comparison(self, node: TaskNode) -> ReplanCandidate:
        return ReplanCandidate(
            action="patch_subgraph",
            estimated_cost=0.6,
            estimated_latency=0.5,
            estimated_success_gain=0.88,
            reason="comparison result incomplete",
            payload=build_compare_patch(node.id),
        )

    def replan_for_missing_summary_coverage(self, node: TaskNode) -> ReplanCandidate:
        return ReplanCandidate(
            action="patch_subgraph",
            estimated_cost=0.55,
            estimated_latency=0.45,
            estimated_success_gain=0.86,
            reason="summary coverage is insufficient",
            payload=build_summary_patch(node.id),
        )

    def build_evidence_patch(self, node_id: str) -> Dict[str, Any]:
        return build_evidence_patch(node_id)

    def build_crosscheck_patch(self, node_id: str) -> Dict[str, Any]:
        return build_crosscheck_patch(node_id)

    def build_normalization_patch(self, node_id: str) -> Dict[str, Any]:
        return build_normalization_patch(node_id)

    def build_schema_patch(self, node_id: str) -> Dict[str, Any]:
        patch = build_schema_patch(node_id)
        patch.update(
            {
                "description": "Refine plan schema to improve deliverable specificity and repository binding.",
                "expected_outputs": [
                    "item_allocation",
                    "quality_criteria",
                    "deliverable_template",
                    "metric_template",
                ],
            }
        )
        return patch

    def build_metric_patch(self, node_id: str) -> Dict[str, Any]:
        patch = build_metric_patch_template(node_id)
        patch.update(
            {
                "description": "Refine metrics so they become measurable and tied to task completion.",
                "expected_outputs": [
                    "metric_template",
                    "numeric_thresholds",
                    "validation_conditions",
                ],
            }
        )
        return patch

    def should_escalate_to_subtree(
        self,
        failure: NodeFailure,
        retry_count: int,
        patch_count: int,
    ) -> bool:
        failure_type = failure.failure_type.strip().lower()
        if failure_type in {
            "intent_misalignment",
            "internal_execution_error",
            "generic_deliverable",
            "non_actionable_metric",
            "repo_binding_weak",
            "schema_semantics_weak",
            "topic_extraction_noisy",
        }:
            return True
        if patch_count >= 2:
            return True
        return retry_count >= 2

    def should_escalate_to_global(self, failure: NodeFailure, subtree_fail_count: int) -> bool:
        failure_type = failure.failure_type.strip().lower()
        if failure_type in {"global_violation", "final_output_not_valid"}:
            return True
        if subtree_fail_count >= 1 and failure_type in {"plan_topic_drift", "repo_binding_weak"}:
            return True
        return subtree_fail_count >= 2

    def patch_for_failure_type(self, node: TaskNode, failure_type: str) -> Optional[Dict[str, Any]]:
        if failure_type in {"evidence", "evidence_error", "echo_retrieval"}:
            return self.build_evidence_patch(node.id)
        if failure_type in {"consistency", "consistency_error", "grounding_error"}:
            return self.build_crosscheck_patch(node.id)
        if failure_type in {"schema", "schema_error", "empty_extraction"}:
            return self.build_normalization_patch(node.id)
        if failure_type == "schema_design_failed":
            return self.build_schema_patch(node.id)
        if failure_type in {"schema_semantics_weak"}:
            return self.build_schema_patch(node.id)
        if failure_type in {"plan_topic_drift"}:
            return self.build_schema_patch(node.id)
        if failure_type in {"generic_deliverable"}:
            return self.build_schema_patch(node.id)
        if failure_type in {"non_actionable_metric"}:
            return self.build_metric_patch(node.id)
        if failure_type in {"repo_binding_weak"}:
            return build_decomposition_patch(node.id)
        if failure_type in {"generic_plan_output"}:
            return build_decomposition_patch(node.id)
        if failure_type in {"low_information_output"}:
            return build_decomposition_patch(node.id)
        if failure_type in {"requirements_analysis_failed", "plan_coverage_incomplete", "final_placeholder_output"}:
            return build_decomposition_patch(node.id)
        if failure_type in {"topic_extraction_noisy"}:
            return build_decomposition_patch(node.id)
        if failure_type in {"final_topic_drift"}:
            return build_decomposition_patch(node.id)
        if failure_type in {"final_answer_missing_structure", "final_answer_not_grounded", "final_query_echo"}:
            return build_final_response_patch(node.id)
        return None

    def enumerate_candidates(
        self,
        node: TaskNode,
        failure: NodeFailure,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> List[ReplanCandidate]:
        reasons_text = " ".join(failure.reasons).lower()
        candidates: List[ReplanCandidate] = []
        candidates.append(
            ReplanCandidate(
                action="retry_same",
                estimated_cost=0.1,
                estimated_latency=0.1,
                estimated_success_gain=0.2,
                reason="cheap retry",
            )
        )
        if node.spec.task_type == "final_response":
            candidates.append(self.replan_for_missing_final_response(node))
        if node.spec.task_type == "comparison":
            candidates.append(self.replan_for_incomplete_comparison(node))
        if node.spec.task_type == "summarization":
            candidates.append(self.replan_for_missing_summary_coverage(node))
        if node.spec.task_type == "calculation":
            candidates.append(
                ReplanCandidate(
                    action="patch_subgraph",
                    estimated_cost=0.45,
                    estimated_latency=0.35,
                    estimated_success_gain=0.84,
                    reason="calculation needs normalization",
                    payload=build_calculation_patch(node.id),
                )
            )
        if node.fallback_agents:
            candidates.append(
                ReplanCandidate(
                    action="switch_agent",
                    estimated_cost=0.2,
                    estimated_latency=0.2,
                    estimated_success_gain=0.4,
                    reason="fallback available",
                )
            )
        if any(x in reasons_text for x in ["semantic", "factual", "source"]):
            candidates.append(
                ReplanCandidate(
                    action="patch_subgraph",
                    estimated_cost=0.6,
                    estimated_latency=0.5,
                    estimated_success_gain=0.8,
                    reason="needs more evidence",
                    payload=build_evidence_patch(node.id),
                )
            )
            candidates.append(
                ReplanCandidate(
                    action="replan_subtree",
                    estimated_cost=1.0,
                    estimated_latency=1.0,
                    estimated_success_gain=0.9,
                    reason="subtree likely mis-specified",
                    payload=build_decomposition_patch(node.id),
                )
            )
        if any(x in reasons_text for x in ["schema", "required", "type"]):
            candidates.append(
                ReplanCandidate(
                    action="patch_subgraph",
                    estimated_cost=0.5,
                    estimated_latency=0.4,
                    estimated_success_gain=0.7,
                    reason="normalize outputs",
                    payload=build_normalization_patch(node.id),
                )
            )
        if any(x in reasons_text for x in ["consistency", "cross_node"]):
            candidates.append(
                ReplanCandidate(
                    action="patch_subgraph",
                    estimated_cost=0.7,
                    estimated_latency=0.7,
                    estimated_success_gain=0.75,
                    reason="cross-check upstream consistency",
                    payload=build_crosscheck_patch(node.id),
                )
            )
        candidates.append(
            ReplanCandidate(
                action="abort",
                estimated_cost=0.0,
                estimated_latency=0.0,
                estimated_success_gain=0.0,
                reason="no viable recovery",
                payload=build_clarification_patch(node.id),
            )
        )
        return candidates

    def decide(
        self,
        node: TaskNode,
        failure: NodeFailure,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> ReplanDecision:
        reasons_text = " ".join(failure.reasons).lower()
        failure_type = failure.failure_type.strip().lower()
        replan_attempts = int(node.metadata.get("replan_attempts", 0))
        patch_attempts = int(node.metadata.get("patch_attempts", 0))
        subtree_fail_count = int(node.metadata.get("subtree_replan_count", 0))
        has_evidence_dep = any(dep.startswith("ev_") for dep in node.dependencies)

        if failure_type in {"requirements_analysis_failed", "topic_extraction_noisy"}:
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason=failure_type,
                failure_type=failure.failure_type,
            )
        if failure_type in {"schema_design_failed", "schema_semantics_weak"}:
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason=f"{failure_type}_requires_rebuild",
                failure_type=failure.failure_type,
            )
        if failure_type in {"generic_deliverable"}:
            return ReplanDecision(
                action="patch_subgraph",
                target_node_id=node.id,
                patch=build_schema_patch(node.id),
                reason="generic_deliverable",
                failure_type=failure.failure_type,
            )
        if failure_type in {"non_actionable_metric"}:
            return ReplanDecision(
                action="patch_subgraph",
                target_node_id=node.id,
                patch=self.build_metric_patch(node.id),
                reason="non_actionable_metric",
                failure_type=failure.failure_type,
            )
        if failure_type in {"repo_binding_weak"}:
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason="repo_binding_weak",
                failure_type=failure.failure_type,
            )
        if failure_type in {"generic_plan_output"}:
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason="generic_plan_output",
                failure_type=failure.failure_type,
            )
        if failure_type in {"plan_coverage_incomplete", "final_placeholder_output", "plan_topic_drift"}:
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason=f"plan_quality_failure:{failure_type}",
                failure_type=failure.failure_type,
            )
        if failure_type in {"final_topic_drift"}:
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason="final_topic_drift",
                failure_type=failure.failure_type,
            )
        if failure_type in {"low_information_output"}:
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason="low_information_output",
                failure_type=failure.failure_type,
            )

        if node.spec.task_type in {"final_response", "aggregation", "summarization", "comparison"} and failure_type in {
            "semantic",
            "final_answer_missing_structure",
            "final_query_echo",
            "intent_misalignment",
        }:
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason=f"aggregation_semantic_failure:{failure.failure_type}",
                failure_type=failure.failure_type,
            )

        if patch_attempts >= 2 and failure.retryable:
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason=f"patch_failed_escalate:{failure.failure_type}",
                failure_type=failure.failure_type,
            )

        if self.should_escalate_to_global(failure=failure, subtree_fail_count=subtree_fail_count):
            return ReplanDecision(
                action="replan_global",
                target_node_id=node.id,
                reason=f"escalate_to_global:{failure.failure_type}",
                failure_type=failure.failure_type,
            )

        if self.should_escalate_to_subtree(
            failure=failure,
            retry_count=replan_attempts,
            patch_count=patch_attempts,
        ):
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason=f"escalate_to_subtree:{failure.failure_type}",
                failure_type=failure.failure_type,
            )

        if failure_type == "internal_execution_error":
            return ReplanDecision(
                action="replan_subtree",
                target_node_id=node.id,
                patch=build_decomposition_patch(node.id),
                reason="internal_execution_error_requires_subtree_replan",
                failure_type=failure.failure_type,
            )

        patch = self.patch_for_failure_type(node=node, failure_type=failure_type)
        if failure.retryable and patch is not None:
            if failure_type == "final_answer_missing_structure" and replan_attempts >= 1:
                return ReplanDecision(
                    action="replan_subtree",
                    target_node_id=node.id,
                    patch=build_decomposition_patch(node.id),
                    reason="final_structure_patch_failed_once",
                    failure_type=failure.failure_type,
                )
            return ReplanDecision(
                action="patch_subgraph",
                target_node_id=node.id,
                patch=patch,
                reason=f"patch_by_failure_type:{failure.failure_type}",
                failure_type=failure.failure_type,
            )

        candidates = self.enumerate_candidates(node=node, failure=failure, tree=tree, context=context)
        scored = sorted(
            candidates,
            key=lambda c: self.scorer.score(c, {"replan_attempts": replan_attempts}),
            reverse=True,
        )

        if (
            failure.retryable
            and not has_evidence_dep
            and replan_attempts < 2
            and (
                "semantic_check_failed" in reasons_text
                or "missing_source" in reasons_text
                or "factual" in reasons_text
            )
        ):
            return ReplanDecision(
                action="patch_subgraph",
                target_node_id=node.id,
                patch=build_evidence_patch(node.id),
                reason="factuality-related failure",
                failure_type=failure.failure_type,
            )

        if failure.retryable and ("final_answer_missing" in reasons_text or "final_answer_not_grounded" in reasons_text):
            return ReplanDecision(
                action="patch_subgraph",
                target_node_id=node.id,
                patch=build_final_response_patch(node.id),
                reason="missing or ungrounded final response",
                failure_type=failure.failure_type,
            )

        if failure.retryable and ("comparison_items_missing" in reasons_text or "comparison_text_missing" in reasons_text):
            return ReplanDecision(
                action="patch_subgraph",
                target_node_id=node.id,
                patch=build_compare_patch(node.id),
                reason="comparison quality issue",
                failure_type=failure.failure_type,
            )

        if failure.retryable and ("summary_too_short" in reasons_text or "summary_missing" in reasons_text):
            return ReplanDecision(
                action="patch_subgraph",
                target_node_id=node.id,
                patch=build_summary_patch(node.id),
                reason="summary coverage issue",
                failure_type=failure.failure_type,
            )

        if failure.retryable and ("calculation_result_not_numeric" in reasons_text or "calculation_trace_missing" in reasons_text):
            return ReplanDecision(
                action="patch_subgraph",
                target_node_id=node.id,
                patch=build_calculation_patch(node.id),
                reason="calculation validation issue",
                failure_type=failure.failure_type,
            )

        if (
            failure.retryable
            and node.fallback_agents
            and "semantic_check_failed" not in reasons_text
            and "factual" not in reasons_text
        ):
            return ReplanDecision(
                action="switch_agent",
                target_node_id=node.id,
                reason="retryable non-semantic failure with available fallback agents",
                failure_type=failure.failure_type,
            )

        if failure.retryable and replan_attempts < 3:
            if scored:
                top = scored[0]
                if top.action in {"patch_subgraph", "replan_subtree", "replan_global", "retry_same", "switch_agent"}:
                    return ReplanDecision(
                        action=top.action,
                        target_node_id=node.id,
                        patch=top.payload or None,
                        reason=top.reason,
                        failure_type=failure.failure_type,
                    )
            return ReplanDecision(
                action="retry_same",
                target_node_id=node.id,
                reason="default retry",
                failure_type=failure.failure_type,
            )

        return ReplanDecision(
            action="abort",
            target_node_id=node.id,
            reason="not retryable",
            failure_type=failure.failure_type,
        )

    def apply(
        self,
        decision: ReplanDecision,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> TaskTree:
        target = tree.nodes.get(decision.target_node_id)
        if target is None:
            return tree

        target.metadata["replan_attempts"] = int(target.metadata.get("replan_attempts", 0)) + 1

        if decision.action == "retry_same":
            target.status = "pending"
            return tree

        if decision.action == "switch_agent":
            if target.fallback_agents:
                next_agent = target.fallback_agents.pop(0)
                if target.assigned_agent:
                    target.fallback_agents.append(target.assigned_agent)
                target.assigned_agent = next_agent
                target.status = "pending"
            return tree

        if decision.action == "patch_subgraph":
            template_name = ""
            if decision.patch:
                template_name = str(decision.patch.get("template", ""))
            target.metadata["patch_attempts"] = int(target.metadata.get("patch_attempts", 0)) + 1
            self.apply_patch_template(
                template_name=template_name or "evidence_retrieval",
                node=target,
                tree=tree,
                context=context,
                failure_type=decision.failure_type,
            )
            return tree

        if decision.action == "replan_subtree":
            target.metadata["subtree_replan_count"] = int(target.metadata.get("subtree_replan_count", 0)) + 1
            self.apply_subtree_replan(
                node=target,
                tree=tree,
                context=context,
                failure_type=decision.failure_type,
            )
            return tree

        if decision.action == "replan_global":
            self.apply_global_replan(
                tree=tree,
                context=context,
                failed_node_id=target.id,
                failure_type=decision.failure_type,
            )
            return tree

        if decision.action == "abort":
            tree.mark_skipped_subtree(decision.target_node_id)
        return tree

    def _apply_patch_subgraph(
        self,
        target: TaskNode,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> None:
        downstream_ids = tree.get_downstream_nodes(target.id)
        removed_ids = {target.id, *downstream_ids}

        evidence_node = self._build_evidence_node(target=target, context=context, tree=tree)
        patched_target = deepcopy(target)
        patched_target.status = "pending"
        patched_target.outputs = {}
        patched_target.dependencies = list(target.dependencies)
        if evidence_node.id not in patched_target.dependencies:
            patched_target.dependencies.append(evidence_node.id)

        regenerated_nodes = [evidence_node, patched_target]
        for node_id in downstream_ids:
            if node_id not in tree.nodes:
                continue
            cloned = deepcopy(tree.nodes[node_id])
            cloned.status = "pending"
            cloned.outputs = {}
            regenerated_nodes.append(cloned)

        tree.replace_subgraph(failed_node_id=target.id, new_nodes=regenerated_nodes)

        # Drop stale outputs for removed subtree so runtime recomputes them.
        for node_id in removed_ids:
            context.node_outputs.pop(node_id, None)

    def apply_subtree_replan(
        self,
        node: TaskNode,
        tree: TaskTree,
        context: ExecutionContext,
        failure_type: str = "",
    ) -> None:
        family = str(tree.metadata.get("task_family", "")).strip().lower()
        if family == "plan":
            lowered = (failure_type or "").strip().lower()
            item_node_ids = [
                node_id for node_id in tree.nodes.keys()
                if node_id.startswith("generate_item") or node_id.startswith("generate_day")
            ]
            if node.id in {"analyze_requirements", "design_plan_schema"} or lowered in {
                "requirements_analysis_failed",
                "schema_design_failed",
            }:
                self._reset_plan_range(
                    tree=tree,
                    context=context,
                    node_ids=[
                        "analyze_requirements",
                        "design_plan_schema",
                        *item_node_ids,
                        "verify_coverage",
                        "final_response",
                    ],
                )
                return
            if (
                node.id.startswith("generate_item")
                or node.id.startswith("generate_day")
                or node.id in {"verify_coverage", "final_response"}
                or lowered in {
                    "plan_topic_drift",
                    "generic_deliverable",
                    "non_actionable_metric",
                    "repo_binding_weak",
                    "generic_plan_output",
                    "low_information_output",
                    "final_topic_drift",
                    "plan_coverage_incomplete",
                }
            ):
                self._reset_plan_range(
                    tree=tree,
                    context=context,
                    node_ids=[
                        *item_node_ids,
                        "verify_coverage",
                        "final_response",
                    ],
                )
                return

        if self.planner is None:
            self._apply_patch_subgraph(target=node, tree=tree, context=context)
            return

        planning_context = PlanningContext(
            user_query=context.global_context.get("query", ""),
            available_agents=[],
            available_tools=[],
            failure_context={"node_id": node.id, "reasons": node.repair_history},
            replan_scope="subtree",
        )
        affected_before = [node.id, *tree.get_downstream_nodes(node.id)]
        before_version = tree.version
        new_nodes = self.planner.replan_subtree(
            tree=tree,
            failed_node_id=node.id,
            context=planning_context,
        )
        if not new_nodes:
            self._apply_patch_subgraph(target=node, tree=tree, context=context)
            return
        tree.replace_subtree(root_node_id=node.id, new_nodes=new_nodes)
        tree.record_graph_delta(
            action="subtree_replaced",
            payload={
                "root_node_id": node.id,
                "new_nodes": [n.id for n in new_nodes],
                "failure_type": failure_type,
                "action": "subtree_replan",
                "patch_template": "decomposition",
                "affected_nodes": affected_before,
                "before_version": before_version,
                "after_version": tree.version,
            },
        )
        for nid in affected_before:
            context.node_outputs.pop(nid, None)

    def _reset_plan_range(
        self,
        tree: TaskTree,
        context: ExecutionContext,
        node_ids: List[str],
    ) -> None:
        before_version = tree.version
        touched: List[str] = []
        for node_id in node_ids:
            node = tree.nodes.get(node_id)
            if node is None:
                continue
            node.status = "pending"
            node.outputs = {}
            touched.append(node_id)
            context.node_outputs.pop(node_id, None)
        if touched:
            tree.version += 1
            tree.record_graph_delta(
                action="plan_range_reset",
                payload={
                    "action": "subtree_replan",
                    "affected_nodes": touched,
                    "before_version": before_version,
                    "after_version": tree.version,
                },
            )

    def apply_global_replan(
        self,
        tree: TaskTree,
        context: ExecutionContext,
        failed_node_id: str = "",
        failure_type: str = "",
    ) -> None:
        if self.planner is None:
            if failed_node_id in tree.nodes:
                self._apply_patch_subgraph(target=tree.nodes[failed_node_id], tree=tree, context=context)
            return

        if not failed_node_id or failed_node_id not in tree.nodes:
            for node_id, node in tree.nodes.items():
                if node.status == "failed":
                    failed_node_id = node_id
                    break
        if not failed_node_id or failed_node_id not in tree.nodes:
            return

        before_version = tree.version
        suffix_ids = {failed_node_id, *tree.get_downstream_nodes(failed_node_id)}
        topo = tree.topo_sort()
        prefix_ids: List[str] = []
        for node_id in topo:
            if node_id in suffix_ids:
                break
            node = tree.nodes.get(node_id)
            if node is not None and node.status == "success":
                prefix_ids.append(node_id)

        preserved_nodes = {node_id: deepcopy(tree.nodes[node_id]) for node_id in prefix_ids if node_id in tree.nodes}

        planning_context = PlanningContext(
            user_query=context.global_context.get("query", ""),
            available_agents=[],
            available_tools=[],
            failure_context={
                "node_id": failed_node_id,
                "failure_type": failure_type,
                "replan_type": "global",
            },
            replan_scope="global",
            task_family=str(tree.metadata.get("task_family", "")),
        )
        rebuilt_tree = self.planner.plan(planning_context)

        merged_nodes: Dict[str, TaskNode] = {}
        for node_id, node in preserved_nodes.items():
            merged_nodes[node_id] = node
        for node_id, node in rebuilt_tree.nodes.items():
            if node_id in merged_nodes:
                continue
            cloned = deepcopy(node)
            cloned.status = "pending"
            cloned.outputs = {}
            merged_nodes[node_id] = cloned

        existing_ids = set(merged_nodes.keys())
        for node in merged_nodes.values():
            node.dependencies = [dep for dep in node.dependencies if dep in existing_ids]

        new_tree = TaskTree(nodes=merged_nodes)
        new_tree.metadata = {**tree.metadata, **rebuilt_tree.metadata}
        new_tree.version = before_version + 1
        new_tree.ensure_single_final_response()
        new_tree.validate()

        removed_ids = [node_id for node_id in tree.nodes if node_id not in new_tree.nodes]
        inserted_ids = [node_id for node_id in new_tree.nodes if node_id not in tree.nodes]

        tree.nodes = new_tree.nodes
        tree.root_ids = new_tree.root_ids
        tree.metadata = new_tree.metadata
        tree.version = new_tree.version
        tree.validate()

        safe_prefix = set(prefix_ids)
        for node_id in list(context.node_outputs.keys()):
            if node_id not in safe_prefix:
                context.node_outputs.pop(node_id, None)

        tree.record_graph_delta(
            action="global_replan",
            payload={
                "failure_type": failure_type,
                "failed_node_id": failed_node_id,
                "action": "global_replan",
                "affected_nodes": sorted(list(suffix_ids)),
                "preserved_prefix": prefix_ids,
                "removed_ids": removed_ids,
                "inserted_ids": inserted_ids,
                "before_version": before_version,
                "after_version": tree.version,
            },
        )

    def apply_patch_template(
        self,
        template_name: str,
        node: TaskNode,
        tree: TaskTree,
        context: ExecutionContext,
        failure_type: str = "",
    ) -> None:
        family = str(tree.metadata.get("task_family", "")).strip().lower()
        if family == "plan" and template_name in {"schema_patch", "metric_patch"}:
            remap = failure_type.strip().lower()
            if not remap:
                remap = "plan_topic_drift" if template_name == "schema_patch" else "non_actionable_metric"
            self.apply_subtree_replan(
                node=node,
                tree=tree,
                context=context,
                failure_type=remap,
            )
            return
        if template_name in {
            "evidence_retrieval",
            "crosscheck",
            "normalization",
            "clarification",
            "summary_patch",
            "compare_patch",
            "calculation_patch",
            "final_response_patch",
            "schema_patch",
            "metric_patch",
        }:
            affected_before = [node.id, *tree.get_downstream_nodes(node.id)]
            before_version = tree.version
            self._apply_patch_subgraph(target=node, tree=tree, context=context)
            tree.record_graph_delta(
                action="patch_template_applied",
                payload={
                    "node_id": node.id,
                    "template": template_name,
                    "failure_type": failure_type,
                    "action": "patch_subgraph",
                    "patch_template": template_name,
                    "affected_nodes": affected_before,
                    "before_version": before_version,
                    "after_version": tree.version,
                },
            )
            return
        if template_name == "decomposition":
            self.apply_subtree_replan(
                node=node,
                tree=tree,
                context=context,
                failure_type=failure_type,
            )
            return
        self._apply_patch_subgraph(target=node, tree=tree, context=context)

    def _build_evidence_node(
        self,
        target: TaskNode,
        context: ExecutionContext,
        tree: TaskTree,
    ) -> TaskNode:
        evidence_id = f"ev_{target.id}_v{tree.version + 1}"
        parser = ConstraintParser()
        constraints = parser.parse(["required_keys:evidence", "non_empty_values"])

        spec = NodeSpec(
            description=f"Retrieve evidence for node {target.id}",
            capability_tag="retrieve",
            task_type="tool_call",
            output_mode="json",
            io=NodeIO(
                input_fields=[
                    FieldSpec(name="query", field_type="string", required=True),
                ],
                output_fields=[
                    FieldSpec(name="evidence", field_type="string", required=True),
                    FieldSpec(name="source", field_type="string", required=False),
                ],
            ),
            constraints=constraints,
            intent=IntentSpec(
                goal=f"Collect evidence for {target.id}",
                success_conditions=["evidence_collected"],
                evidence_requirements=["evidence"],
                output_semantics={"evidence": "supporting evidence text"},
            ),
            intent_tags=["search", "evidence"],
        )
        return TaskNode(
            id=evidence_id,
            spec=spec,
            dependencies=list(target.dependencies),
            assigned_agent="retrieve_agent",
            fallback_agents=[],
            status="pending",
            inputs={"query": context.global_context.get("query", "")},
            execution_policy=ExecutionPolicy(max_retry=1, retryable=True),
            metadata={"generated_by": "constraint_aware_replanner"},
        )
