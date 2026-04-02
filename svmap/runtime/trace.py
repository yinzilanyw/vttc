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
