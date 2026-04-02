from __future__ import annotations

from dataclasses import dataclass

from svmap.models import ExecutionReport


@dataclass
class MetricsSummary:
    task_success: bool
    node_success_rate: float
    verification_failure_count: int
    retry_count: int
    replan_count: int
    avg_attempts_per_node: float


class MetricsCollector:
    def summarize(self, report: ExecutionReport) -> MetricsSummary:
        total_nodes = max(len(report.node_records), 1)
        success_nodes = sum(1 for r in report.node_records.values() if r.status == "success")
        total_attempts = sum(r.attempts for r in report.node_records.values())
        return MetricsSummary(
            task_success=report.success,
            node_success_rate=success_nodes / total_nodes,
            verification_failure_count=report.verification_failures,
            retry_count=report.total_retries,
            replan_count=report.replan_count,
            avg_attempts_per_node=total_attempts / total_nodes,
        )
