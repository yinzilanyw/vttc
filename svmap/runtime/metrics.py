from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from svmap.models import ExecutionReport


@dataclass
class MetricsSummary:
    task_success: bool = False
    task_success_rate: float = 0.0
    node_success_rate: float = 0.0
    verification_failure_count: int = 0
    verification_precision: float = 0.0
    verification_recall: float = 0.0
    false_positive_rate: float = 0.0
    false_negative_rate: float = 0.0
    retry_count: int = 0
    replan_count: int = 0
    recovery_rate: float = 0.0
    success_after_first_failure: float = 0.0
    subtree_replan_success_rate: float = 0.0
    global_replan_success_rate: float = 0.0
    patch_success_rate_by_type: Dict[str, float] = field(default_factory=dict)
    avg_saved_downstream_nodes: float = 0.0
    parallelizable_node_ratio: float = 0.0
    avg_cost_saved_vs_full_rerun: float = 0.0
    avg_attempts_per_node: float = 0.0
    task_family_breakdown: Dict[str, float] = field(default_factory=dict)
    final_response_success_rate: float = 0.0
    aggregation_success_rate: float = 0.0
    multitask_generalization_score: float = 0.0
    placeholder_output_rate: float = 0.0
    plan_structure_pass_rate: float = 0.0
    semantic_alignment_rate: float = 0.0
    coverage_verification_pass_rate: float = 0.0
    topic_drift_rate: float = 0.0
    plan_quality_pass_rate: float = 0.0


class MetricsCollector:
    def summarize(self, report: ExecutionReport) -> MetricsSummary:
        total_nodes = max(len(report.node_records), 1)
        success_nodes = sum(1 for r in report.node_records.values() if r.status == "success")
        total_attempts = sum(r.attempts for r in report.node_records.values())
        total_failures = max(report.verification_failures, 1)
        recovered_nodes = sum(
            1
            for r in report.node_records.values()
            if r.status == "success" and r.attempts > 1
        )
        patch_actions = [a for a in report.replan_actions if "patch" in a]
        subtree_actions = [a for a in report.replan_actions if a == "replan_subtree"]
        global_actions = [a for a in report.replan_actions if a == "replan_global"]
        patch_success = 1.0 if patch_actions and report.success else (0.0 if patch_actions else 1.0)
        patch_success_by_type: Dict[str, float] = {}
        for action in sorted(set(patch_actions)):
            patch_success_by_type[action] = 1.0 if report.success else 0.0
        node_task_types = report.node_task_types or {}
        final_nodes = [nid for nid, t in node_task_types.items() if t == "final_response"]
        aggregation_nodes = [nid for nid, t in node_task_types.items() if t in {"summarization", "comparison", "aggregation"}]
        final_ok = 0
        for node_id in final_nodes:
            rec = report.node_records.get(node_id)
            if rec and rec.status == "success":
                final_ok += 1
        agg_ok = 0
        for node_id in aggregation_nodes:
            rec = report.node_records.get(node_id)
            if rec and rec.status == "success":
                agg_ok += 1
        family = report.task_family or "unknown"
        placeholder_failures = sum(
            1
            for rec in report.node_records.values()
            if rec.failure_type in {"final_placeholder_output", "low_information_output"}
        )
        intent_failures = sum(
            1 for rec in report.node_records.values() if rec.failure_type in {"intent_misalignment"}
        )
        topic_drift_failures = sum(
            1
            for rec in report.node_records.values()
            if rec.failure_type in {"plan_topic_drift", "final_topic_drift"}
        )
        verify_coverage_nodes = [nid for nid in node_task_types if nid == "verify_coverage"]
        verify_coverage_ok = sum(
            1
            for nid in verify_coverage_nodes
            if (report.node_records.get(nid) and report.node_records[nid].status == "success")
        )
        plan_structure_fail = report.failure_summary.get("final_answer_missing_structure", 0) + report.failure_summary.get(
            "plan_coverage_incomplete",
            0,
        )

        return MetricsSummary(
            task_success=report.success,
            task_success_rate=1.0 if report.success else 0.0,
            node_success_rate=success_nodes / total_nodes,
            verification_failure_count=report.verification_failures,
            verification_precision=success_nodes / (success_nodes + total_failures),
            verification_recall=success_nodes / total_nodes,
            false_positive_rate=0.0,
            false_negative_rate=0.0,
            retry_count=report.total_retries,
            replan_count=report.replan_count,
            recovery_rate=recovered_nodes / total_nodes,
            success_after_first_failure=1.0 if recovered_nodes > 0 else 0.0,
            subtree_replan_success_rate=(
                1.0 if subtree_actions and report.success else (0.0 if subtree_actions else 0.0)
            ),
            global_replan_success_rate=(
                1.0 if global_actions and report.success else (0.0 if global_actions else 0.0)
            ),
            patch_success_rate_by_type=patch_success_by_type or {"patch_subgraph": patch_success},
            avg_saved_downstream_nodes=float(
                report.structural_savings.get("avg_saved_downstream_nodes", 0.0)
            ),
            parallelizable_node_ratio=float(
                report.structural_savings.get("parallelizable_node_ratio", 0.0)
            ),
            avg_cost_saved_vs_full_rerun=float(
                report.structural_savings.get("avg_cost_saved_vs_full_rerun", 0.0)
            ),
            avg_attempts_per_node=total_attempts / total_nodes,
            task_family_breakdown={family: 1.0 if report.success else 0.0},
            final_response_success_rate=(final_ok / max(len(final_nodes), 1)),
            aggregation_success_rate=(agg_ok / max(len(aggregation_nodes), 1)),
            multitask_generalization_score=(
                0.4 * (1.0 if report.success else 0.0)
                + 0.3 * (final_ok / max(len(final_nodes), 1))
                + 0.3 * (agg_ok / max(len(aggregation_nodes), 1))
            ),
            placeholder_output_rate=placeholder_failures / total_nodes,
            plan_structure_pass_rate=1.0 if plan_structure_fail == 0 else 0.0,
            semantic_alignment_rate=max(0.0, 1.0 - (intent_failures / total_nodes)),
            coverage_verification_pass_rate=(
                verify_coverage_ok / max(len(verify_coverage_nodes), 1)
                if verify_coverage_nodes
                else (1.0 if family != "plan" else 0.0)
            ),
            topic_drift_rate=topic_drift_failures / total_nodes,
            plan_quality_pass_rate=(
                1.0
                if (
                    family != "plan"
                    or (
                        plan_structure_fail == 0
                        and topic_drift_failures == 0
                        and (verify_coverage_ok == len(verify_coverage_nodes) if verify_coverage_nodes else True)
                    )
                )
                else 0.0
            ),
        )

    def collect_verification_quality(self, traces: List[Dict[str, Any]]) -> Dict[str, float]:
        violations = [e for e in traces if e.get("event_type") == "constraint_violation"]
        checks = [e for e in traces if e.get("event_type", "").startswith("node_")]
        denom = max(len(checks), 1)
        precision = len(violations) / denom
        return {
            "verification_precision": max(0.0, 1.0 - precision),
            "verification_recall": precision,
        }

    def collect_replan_effectiveness(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        decisions = [e for e in traces if e.get("event_type") == "replan_decision"]
        replaced = [e for e in traces if e.get("event_type") == "subtree_replaced"]
        return {
            "replan_count": len(decisions),
            "subtree_replace_count": len(replaced),
            "actions": [e.get("payload", {}).get("action", "") for e in decisions],
        }

    def collect_structural_benefits(self, traces: List[Dict[str, Any]]) -> Dict[str, float]:
        graph_deltas = [e for e in traces if e.get("event_type") == "graph_delta_recorded"]
        return {
            "avg_saved_downstream_nodes": float(
                sum(e.get("payload", {}).get("saved_downstream_nodes", 0) for e in graph_deltas)
                / max(len(graph_deltas), 1)
            ),
            "parallelizable_node_ratio": 0.0,
            "avg_cost_saved_vs_full_rerun": 0.0,
        }

    def summarize_by_task_family(self, reports: List[ExecutionReport]) -> Dict[str, Any]:
        family_to_reports: Dict[str, List[ExecutionReport]] = {}
        for report in reports:
            family = report.task_family or "unknown"
            family_to_reports.setdefault(family, []).append(report)

        summary: Dict[str, Any] = {}
        for family, items in family_to_reports.items():
            success_count = sum(1 for item in items if item.success)
            avg_replans = sum(item.replan_count for item in items) / max(len(items), 1)
            avg_retries = sum(item.total_retries for item in items) / max(len(items), 1)
            summary[family] = {
                "count": len(items),
                "task_success_rate": success_count / max(len(items), 1),
                "avg_replan_count": avg_replans,
                "avg_retry_count": avg_retries,
            }
        return summary
