from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .constraints import Constraint


@dataclass
class FieldSpec:
    name: str
    field_type: str
    required: bool = True
    description: str = ""


@dataclass
class NodeIO:
    input_fields: List[FieldSpec] = field(default_factory=list)
    output_fields: List[FieldSpec] = field(default_factory=list)

    def required_output_field_names(self) -> List[str]:
        return [f.name for f in self.output_fields if f.required]


@dataclass
class IntentSpec:
    goal: str
    success_conditions: List[str] = field(default_factory=list)
    evidence_requirements: List[str] = field(default_factory=list)
    dependency_assumptions: List[str] = field(default_factory=list)
    output_semantics: Dict[str, str] = field(default_factory=dict)
    response_style: str = "plain"
    aggregation_requirements: List[str] = field(default_factory=list)
    propagates_to_children: bool = True
    required_upstream_intents: List[str] = field(default_factory=list)
    child_completion_criteria: List[str] = field(default_factory=list)


@dataclass
class NodeSpec:
    description: str
    capability_tag: str
    io: NodeIO
    constraints: List[Constraint] = field(default_factory=list)
    intent: Optional[IntentSpec] = None
    intent_tags: List[str] = field(default_factory=list)
    task_type: str = "reasoning"
    output_mode: str = "text"
    answer_role: str = "intermediate"


@dataclass
class ExecutionPolicy:
    max_retry: int = 2
    retryable: bool = True


@dataclass
class TaskNode:
    id: str
    spec: NodeSpec
    dependencies: List[str] = field(default_factory=list)

    assigned_agent: Optional[str] = None
    fallback_agents: List[str] = field(default_factory=list)

    status: str = "pending"
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)

    execution_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    metadata: Dict[str, Any] = field(default_factory=dict)
    parent_intent_ids: List[str] = field(default_factory=list)
    intent_status: str = "unknown"
    repair_history: List[str] = field(default_factory=list)

    @property
    def max_retry(self) -> int:
        return self.execution_policy.max_retry

    @max_retry.setter
    def max_retry(self, value: int) -> None:
        self.execution_policy.max_retry = value

    def candidate_agents(self) -> List[str]:
        candidates: List[str] = []
        if self.assigned_agent:
            candidates.append(self.assigned_agent)
        for agent in self.fallback_agents:
            if agent not in candidates:
                candidates.append(agent)
        return candidates

    def primary_goal(self) -> Optional[str]:
        if self.spec.intent is None:
            return None
        return self.spec.intent.goal

    def requires_evidence(self) -> bool:
        if self.spec.intent and self.spec.intent.evidence_requirements:
            return True
        return any(getattr(c, "constraint_type", "") == "factuality" for c in self.spec.constraints)

    def mark_intent_aligned(self) -> None:
        self.intent_status = "aligned"

    def mark_intent_violated(self, reason: str) -> None:
        self.intent_status = "violated"
        self.repair_history.append(reason)

    def is_final_response(self) -> bool:
        return self.spec.answer_role == "final" or self.spec.task_type == "final_response"

    def is_aggregation_node(self) -> bool:
        return self.spec.task_type in {"aggregation", "synthesis", "summarization", "comparison"}
