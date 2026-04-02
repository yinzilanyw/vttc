from __future__ import annotations

from abc import ABC, abstractmethod

from svmap.models import TaskNode, TaskTree

from .registry import AgentRegistry, AgentSpec


class AssignmentStrategy(ABC):
    @abstractmethod
    def assign(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        raise NotImplementedError


class CapabilityBasedAssigner(AssignmentStrategy):
    intent_match_weight: float = 1.0
    repair_match_weight: float = 1.0

    def _score(self, spec: AgentSpec) -> float:
        cost = max(spec.cost_weight, 1e-6)
        latency = max(spec.latency_weight, 1e-6)
        return spec.reliability / (cost * latency)

    def _score_for_node(self, spec: AgentSpec, node: TaskNode) -> float:
        base = self._score(spec)
        intent_bonus = 0.0
        if node.spec.intent_tags:
            overlap = set(node.spec.intent_tags).intersection(set(spec.supported_intent_tags))
            if overlap:
                intent_bonus += self.intent_match_weight * (len(overlap) / max(len(node.spec.intent_tags), 1))
        hist_bonus = spec.historical_success_by_capability.get(node.spec.capability_tag, 0.0)
        return base + intent_bonus + hist_bonus

    def assign(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        for node in tree.nodes.values():
            candidates = registry.rank_candidates(node)
            if not candidates:
                continue
            ranked = sorted(candidates, key=lambda spec: self._score_for_node(spec, node), reverse=True)
            node.assigned_agent = ranked[0].name
            node.fallback_agents = [spec.name for spec in ranked[1:]]
        return tree

    def assign_with_intent(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        for node in tree.nodes.values():
            candidates = registry.find_candidates_for_intent(
                capability_tag=node.spec.capability_tag,
                intent=node.spec.intent,
            )
            if not candidates:
                continue
            ranked = sorted(candidates, key=lambda spec: self._score_for_node(spec, node), reverse=True)
            node.assigned_agent = ranked[0].name
            node.fallback_agents = [spec.name for spec in ranked[1:]]
        return tree

    def reassign_after_failure(
        self,
        node: TaskNode,
        failure_type: str,
        registry: AgentRegistry,
    ) -> TaskNode:
        capability_tag = node.spec.capability_tag
        candidates = registry.find_candidates(capability_tag)
        if not candidates:
            return node

        repair_bonus_map = {
            "verification_failed": "verification",
            "runtime_error": "runtime",
            "planner_error": "planning",
        }
        specialty = repair_bonus_map.get(failure_type, "")

        def score(spec: AgentSpec) -> float:
            base = self._score(spec)
            bonus = self.repair_match_weight if specialty and specialty in spec.repair_specialties else 0.0
            return base + bonus

        ranked = sorted(candidates, key=score, reverse=True)
        node.assigned_agent = ranked[0].name
        node.fallback_agents = [spec.name for spec in ranked[1:]]
        return node
