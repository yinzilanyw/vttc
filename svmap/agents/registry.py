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

    def find_candidates(self, capability_tag: str) -> List[AgentSpec]:
        return [
            spec
            for spec in self._specs.values()
            if capability_tag in spec.capabilities
        ]

    def find_candidates_for_intent(
        self,
        capability_tag: str,
        intent: IntentSpec | None,
    ) -> List[AgentSpec]:
        candidates = self.find_candidates(capability_tag)
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
        def score(spec: AgentSpec) -> float:
            base = spec.reliability / max(spec.cost_weight * spec.latency_weight, 1e-6)
            hist = spec.historical_success_by_capability.get(node.spec.capability_tag, spec.reliability)
            return 0.7 * base + 0.3 * hist

        return sorted(candidates, key=score, reverse=True)

    def get_repair_capable_agents(self) -> List[AgentSpec]:
        return [spec for spec in self._specs.values() if spec.repair_specialties]
