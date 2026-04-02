from __future__ import annotations

from abc import ABC, abstractmethod

from svmap.models import TaskTree

from .registry import AgentRegistry, AgentSpec


class AssignmentStrategy(ABC):
    @abstractmethod
    def assign(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        raise NotImplementedError


class CapabilityBasedAssigner(AssignmentStrategy):
    def _score(self, spec: AgentSpec) -> float:
        cost = max(spec.cost_weight, 1e-6)
        latency = max(spec.latency_weight, 1e-6)
        return spec.reliability / (cost * latency)

    def assign(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        for node in tree.nodes.values():
            candidates = registry.find_candidates(node.spec.capability_tag)
            if not candidates:
                continue
            ranked = sorted(candidates, key=self._score, reverse=True)
            node.assigned_agent = ranked[0].name
            node.fallback_agents = [spec.name for spec in ranked[1:]]
        return tree
