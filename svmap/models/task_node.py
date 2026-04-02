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
class NodeSpec:
    description: str
    capability_tag: str
    io: NodeIO
    constraints: List[Constraint] = field(default_factory=list)


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
