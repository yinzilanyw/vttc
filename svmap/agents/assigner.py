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
    task_type_weight: float = 1.0
    output_mode_weight: float = 0.8
    final_response_weight: float = 1.2
    task_type_preference_bonus: float = 1.5
    plan_task_preference_bonus: float = 1.8
    node_responsibility_bonus: float = 1.6
    node_responsibility_penalty: float = 1.2

    def _score(self, spec: AgentSpec) -> float:
        cost = max(spec.cost_weight, 1e-6)
        latency = max(spec.latency_weight, 1e-6)
        return spec.reliability / (cost * latency)

    def preferred_agents_for_task_type(self, task_type: str, node_id: str = "") -> list[str]:
        node_key = node_id.lower().strip()
        if node_key == "analyze_requirements":
            return ["reason_agent", "synthesize_agent"]
        if node_key == "design_plan_schema":
            return ["reason_agent", "verify_agent", "synthesize_agent"]
        if node_key.startswith("generate_day"):
            return ["synthesize_agent", "reason_agent"]
        if node_key == "verify_coverage":
            return ["verify_agent", "reason_agent", "synthesize_agent"]
        if node_key == "final_response":
            return ["synthesize_agent", "reason_agent"]
        mapping = {
            "reasoning": ["reason_agent", "synthesize_agent"],
            "verification": ["verify_agent", "reason_agent"],
            "aggregation": ["synthesize_agent", "reason_agent"],
            "final_response": ["synthesize_agent"],
            "comparison": ["compare_agent", "reason_agent"],
            "calculation": ["calculate_agent", "reason_agent"],
            "extraction": ["extract_agent", "reason_agent"],
            "tool_call": ["retrieve_agent"],
        }
        return mapping.get(task_type, [])

    def _preferred_agents_for_node(self, node: TaskNode, task_family: str) -> list[str]:
        if task_family != "plan":
            return self.preferred_agents_for_task_type(node.spec.task_type, node_id=node.id)
        node_id = node.id.lower()
        if node_id == "analyze_requirements":
            return ["reason_agent"]
        if node_id == "design_plan_schema":
            return ["reason_agent"]
        if node_id.startswith("generate_day"):
            return ["synthesize_agent"]
        if node_id == "verify_coverage":
            return ["verify_agent"]
        if node.is_final_response():
            return ["synthesize_agent"]
        return self.preferred_agents_for_task_type(node.spec.task_type, node_id=node.id)

    def _node_responsibility_adjustment(self, spec: AgentSpec, node: TaskNode, task_family: str) -> float:
        if task_family != "plan":
            return 0.0
        node_id = node.id.lower()
        bonus = 0.0
        if node_id in {"analyze_requirements", "design_plan_schema"}:
            if spec.name == "reason_agent" or "reason" in spec.capabilities:
                bonus += self.node_responsibility_bonus
            if spec.name == "retrieve_agent" or "retrieve" in spec.capabilities:
                bonus -= self.node_responsibility_penalty
        elif node_id.startswith("generate_day"):
            if spec.name == "synthesize_agent" or "synthesize" in spec.capabilities:
                bonus += self.node_responsibility_bonus
            if spec.name == "verify_agent" or "verify" in spec.capabilities:
                bonus -= self.node_responsibility_penalty * 0.6
        elif node_id == "verify_coverage":
            if spec.name == "verify_agent" or "verify" in spec.capabilities:
                bonus += self.node_responsibility_bonus
            if spec.name == "synthesize_agent" or "synthesize" in spec.capabilities:
                bonus -= self.node_responsibility_penalty
        elif node.is_final_response():
            if spec.name == "synthesize_agent" or "synthesize" in spec.capabilities:
                bonus += self.node_responsibility_bonus
            if spec.name == "verify_agent" or "verify" in spec.capabilities:
                bonus -= self.node_responsibility_penalty * 0.5
        return bonus

    def _score_for_node(self, spec: AgentSpec, node: TaskNode, task_family: str = "") -> float:
        base = self._score(spec)
        intent_bonus = 0.0
        if node.spec.intent_tags:
            overlap = set(node.spec.intent_tags).intersection(set(spec.supported_intent_tags))
            if overlap:
                intent_bonus += self.intent_match_weight * (len(overlap) / max(len(node.spec.intent_tags), 1))
        hist_bonus = spec.historical_success_by_capability.get(node.spec.capability_tag, 0.0)
        task_type_bonus = self.task_type_weight if (not spec.task_types or node.spec.task_type in spec.task_types) else 0.0
        output_mode_bonus = self.output_mode_weight if (not spec.output_modes or node.spec.output_mode in spec.output_modes) else 0.0
        final_bonus = self.final_response_weight if node.is_final_response() and ("final_response" in spec.task_types or "synthesize" in spec.capabilities) else 0.0
        pref_bonus = 0.0
        preferred = self._preferred_agents_for_node(node=node, task_family=task_family)
        if spec.name in preferred and preferred:
            rank = preferred.index(spec.name)
            bonus = self.plan_task_preference_bonus if task_family == "plan" else self.task_type_preference_bonus
            pref_bonus = bonus * (1.0 - rank / max(len(preferred), 1))
        responsibility_bonus = self._node_responsibility_adjustment(spec=spec, node=node, task_family=task_family)
        return (
            base
            + intent_bonus
            + hist_bonus
            + task_type_bonus
            + output_mode_bonus
            + final_bonus
            + pref_bonus
            + responsibility_bonus
        )

    def assign(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        return self.assign_by_capability(tree=tree, registry=registry)

    def assign_with_intent(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        return self.assign_by_capability(tree=tree, registry=registry)

    def assign_by_capability(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        task_family = str(tree.metadata.get("task_family", "")).strip().lower()
        for node in tree.nodes.values():
            candidates = registry.find_candidates_for_intent(
                capability_tag=node.spec.capability_tag,
                intent=node.spec.intent,
            )
            if not candidates:
                candidates = registry.find_by_task_type(node.spec.task_type)
            candidates = [
                spec
                for spec in candidates
                if (not spec.output_modes or node.spec.output_mode in spec.output_modes)
            ]
            if not candidates:
                continue
            ranked = sorted(
                candidates,
                key=lambda spec: self._score_for_node(spec, node, task_family=task_family),
                reverse=True,
            )
            node.assigned_agent = ranked[0].name
            node.fallback_agents = [spec.name for spec in ranked[1:]]
        return self.assign_final_response_node(tree=tree, registry=registry)

    def assign_final_response_node(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        final_candidates = registry.find_final_response_agents()
        if not final_candidates:
            return tree
        for node in tree.nodes.values():
            if not node.is_final_response():
                continue
            ranked = sorted(
                final_candidates,
                key=lambda spec: self._score_for_node(spec, node, task_family=str(tree.metadata.get("task_family", "")).strip().lower()),
                reverse=True,
            )
            node.assigned_agent = ranked[0].name
            node.fallback_agents = [spec.name for spec in ranked[1:]]
        return tree

    def reassign_for_node_type(self, node: TaskNode, registry: AgentRegistry) -> TaskNode:
        candidates = registry.find_by_task_type(node.spec.task_type)
        if not candidates:
            candidates = registry.find_by_capability(node.spec.capability_tag)
        if not candidates:
            return node
        ranked = sorted(candidates, key=lambda spec: self._score_for_node(spec, node), reverse=True)
        node.assigned_agent = ranked[0].name
        node.fallback_agents = [spec.name for spec in ranked[1:]]
        return node

    def reassign_after_failure(
        self,
        node: TaskNode,
        failure_type: str,
        registry: AgentRegistry,
    ) -> TaskNode:
        capability_tag = node.spec.capability_tag
        candidates = registry.find_by_capability(capability_tag)
        if not candidates:
            return self.reassign_for_node_type(node=node, registry=registry)

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
