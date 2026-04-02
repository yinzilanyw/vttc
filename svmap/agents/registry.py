from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .base import BaseAgent


@dataclass
class AgentSpec:
    name: str
    capabilities: List[str]
    supported_tools: List[str] = field(default_factory=list)
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
