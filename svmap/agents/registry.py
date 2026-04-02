from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from svmap.models import IntentSpec, TaskNode

from .base import BaseAgent


@dataclass
class AgentSpec:
    name: str
    capabilities: List[str]
    supported_tools: List[str] = field(default_factory=list)
    supported_intent_tags: List[str] = field(default_factory=list)
    repair_specialties: List[str] = field(default_factory=list)
    task_types: List[str] = field(default_factory=list)
    output_modes: List[str] = field(default_factory=list)
    historical_success_by_capability: Dict[str, float] = field(default_factory=dict)
    reliability: float = 1.0
    cost_weight: float = 1.0
    latency_weight: float = 1.0
    metadata: Dict[str, object] = field(default_factory=dict)


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: Dict[str, BaseAgent] = {}
        self._specs: Dict[str, AgentSpec] = {}

    def register(self, name: str, agent: BaseAgent, spec: AgentSpec) -> None:
        self._agents[name] = agent
        self._specs[name] = spec

    def get(self, name: str) -> BaseAgent:
        return self._agents[name]

    def has(self, name: str) -> bool:
        return name in self._agents

    def get_spec(self, name: str) -> AgentSpec:
        return self._specs[name]

    def names(self) -> List[str]:
        return list(self._specs.keys())

    def find_candidates(self, capability_tag: str) -> List[AgentSpec]:
        return self.find_by_capability(capability_tag)

    def find_by_capability(self, capability_tag: str) -> List[AgentSpec]:
        if not capability_tag:
            return list(self._specs.values())
        return [
            spec
            for spec in self._specs.values()
            if capability_tag in spec.capabilities
            or "*" in spec.capabilities
        ]

    def find_by_task_type(self, node_type: str) -> List[AgentSpec]:
        return [
            spec
            for spec in self._specs.values()
            if not spec.task_types or node_type in spec.task_types
        ]

    def find_final_response_agents(self) -> List[AgentSpec]:
        candidates = [
            spec
            for spec in self._specs.values()
            if "final_response" in spec.task_types
            or "synthesize" in spec.capabilities
            or "final" in spec.supported_intent_tags
        ]
        return sorted(candidates, key=lambda spec: spec.reliability, reverse=True)

    def find_candidates_for_intent(
        self,
        capability_tag: str,
        intent: IntentSpec | None,
    ) -> List[AgentSpec]:
        candidates = self.find_by_capability(capability_tag)
        if intent is None:
            return candidates
        if not intent.goal:
            return candidates
        target_tag = capability_tag
        filtered = [
            spec
            for spec in candidates
            if not spec.supported_intent_tags or target_tag in spec.supported_intent_tags
        ]
        return filtered or candidates

    def rank_candidates(self, node: TaskNode) -> List[AgentSpec]:
        candidates = self.find_candidates_for_intent(
            capability_tag=node.spec.capability_tag,
            intent=node.spec.intent,
        )
        if not candidates:
            candidates = self.find_by_task_type(node.spec.task_type)
        if not candidates:
            candidates = list(self._specs.values())
        candidates = [
            spec
            for spec in candidates
            if (not spec.task_types or node.spec.task_type in spec.task_types)
            and (not spec.output_modes or node.spec.output_mode in spec.output_modes)
        ]
        if not candidates:
            candidates = self.find_by_task_type(node.spec.task_type) or list(self._specs.values())

        def score(spec: AgentSpec) -> float:
            base = spec.reliability / max(spec.cost_weight * spec.latency_weight, 1e-6)
            hist = spec.historical_success_by_capability.get(node.spec.capability_tag, spec.reliability)
            type_bonus = 0.2 if node.spec.task_type in spec.task_types else 0.0
            mode_bonus = 0.1 if node.spec.output_mode in spec.output_modes else 0.0
            final_bonus = 0.25 if node.is_final_response() and "final_response" in spec.task_types else 0.0
            return 0.7 * base + 0.3 * hist + type_bonus + mode_bonus + final_bonus

        return sorted(candidates, key=score, reverse=True)

    def get_repair_capable_agents(self) -> List[AgentSpec]:
        return [spec for spec in self._specs.values() if spec.repair_specialties]
