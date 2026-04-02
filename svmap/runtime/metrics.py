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
    patch_success_rate_by_type: Dict[str, float] = field(default_factory=dict)
    avg_saved_downstream_nodes: float = 0.0
    parallelizable_node_ratio: float = 0.0
    avg_cost_saved_vs_full_rerun: float = 0.0
    avg_attempts_per_node: float = 0.0


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
        patch_success = 1.0 if patch_actions and report.success else (0.0 if patch_actions else 1.0)

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
            patch_success_rate_by_type={"patch_subgraph": patch_success},
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
