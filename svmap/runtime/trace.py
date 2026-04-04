from __future__ import annotations

import json
import time
from typing import Any, Dict, List


class TraceLogger:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def log_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            }
        )

    def export_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.events, f, ensure_ascii=False, indent=2)

    def log_graph_delta(
        self,
        before_version: int,
        after_version: int,
        payload: Dict[str, Any],
    ) -> None:
        self.log_event(
            "graph_delta_recorded",
            {
                "before_version": before_version,
                "after_version": after_version,
                **payload,
            },
        )

    def log_constraint_violation(
        self,
        node_id: str,
        failure_type: str,
        reasons: List[str],
        repair_hint: str = "",
        violation_scope: List[str] | None = None,
        replan_action: str = "",
        graph_delta_summary: str = "",
    ) -> None:
        self.log_event(
            "constraint_violation",
            {
                "node_id": node_id,
                "failure_type": failure_type,
                "reasons": reasons,
                "repair_hint": repair_hint,
                "violation_scope": violation_scope or [],
                "replan_action": replan_action,
                "graph_delta_summary": graph_delta_summary,
            },
        )

    def log_plan_quality_failure(
        self,
        node_id: str,
        failure_type: str,
        reasons: List[str],
        repair_hint: str = "",
        replan_action: str = "",
        graph_delta_summary: str = "",
    ) -> None:
        self.log_event(
            "plan_quality_failure",
            {
                "node_id": node_id,
                "failure_type": failure_type,
                "reasons": reasons,
                "repair_hint": repair_hint,
                "replan_action": replan_action,
                "graph_delta_summary": graph_delta_summary,
            },
        )

    def export_case_study(self, path: str) -> None:
        case_payload = {
            "summary": {
                "total_events": len(self.events),
                "event_types": sorted({e.get("event_type", "") for e in self.events}),
            },
            "events": self.events,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(case_payload, f, ensure_ascii=False, indent=2)

    def export_graph_events(self, path: str) -> None:
        graph_events = [
            e
            for e in self.events
            if e.get("event_type")
            in {
                "subtree_replaced",
                "graph_delta_recorded",
                "replan_decision",
                "constraint_violation",
                "plan_quality_failure",
            }
        ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(graph_events, f, ensure_ascii=False, indent=2)
