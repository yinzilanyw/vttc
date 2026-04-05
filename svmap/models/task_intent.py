from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskIntentSpec:
    primary_intent: str
    secondary_intents: List[str] = field(default_factory=list)
    operators: List[str] = field(default_factory=list)
    shape: Optional[str] = None
    item_count: Optional[int] = None
    item_label: Optional[str] = None
    structured_output: bool = False
    grounded: bool = False
    multi_entity: bool = False
    decomposition_needed: bool = False
    topics: List[str] = field(default_factory=list)
    must_cover_topics: List[str] = field(default_factory=list)
    required_fields: List[str] = field(default_factory=list)
    quality_targets: Dict[str, bool] = field(default_factory=dict)
    raw_signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_intent": self.primary_intent,
            "secondary_intents": list(self.secondary_intents),
            "operators": list(self.operators),
            "shape": self.shape,
            "item_count": self.item_count,
            "item_label": self.item_label,
            "structured_output": bool(self.structured_output),
            "grounded": bool(self.grounded),
            "multi_entity": bool(self.multi_entity),
            "decomposition_needed": bool(self.decomposition_needed),
            "topics": list(self.topics),
            "must_cover_topics": list(self.must_cover_topics),
            "required_fields": list(self.required_fields),
            "quality_targets": dict(self.quality_targets),
            "raw_signals": dict(self.raw_signals),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskIntentSpec":
        payload = dict(data or {})
        return cls(
            primary_intent=str(payload.get("primary_intent", "qa")),
            secondary_intents=list(payload.get("secondary_intents", []) or []),
            operators=list(payload.get("operators", []) or []),
            shape=payload.get("shape"),
            item_count=payload.get("item_count"),
            item_label=payload.get("item_label"),
            structured_output=bool(payload.get("structured_output", False)),
            grounded=bool(payload.get("grounded", False)),
            multi_entity=bool(payload.get("multi_entity", False)),
            decomposition_needed=bool(payload.get("decomposition_needed", False)),
            topics=list(payload.get("topics", []) or []),
            must_cover_topics=list(payload.get("must_cover_topics", []) or []),
            required_fields=list(payload.get("required_fields", []) or []),
            quality_targets=dict(payload.get("quality_targets", {}) or {}),
            raw_signals=dict(payload.get("raw_signals", {}) or {}),
        )
