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
    task_type_weight: float = 0.8
    output_mode_weight: float = 0.8
    final_response_weight: float = 1.2
    role_preference_bonus: float = 1.6
    operator_preference_bonus: float = 1.1

    def _score(self, spec: AgentSpec) -> float:
        cost = max(spec.cost_weight, 1e-6)
        latency = max(spec.latency_weight, 1e-6)
        return spec.reliability / (cost * latency)

    def _resolve_node_role(self, node: TaskNode) -> str:
        metadata_role = str(node.metadata.get("node_role", "")).strip().lower()
        if metadata_role:
            return metadata_role
        node_id = node.id.lower()
        if node_id == "analyze_requirements":
            return "requirements_analysis"
        if node_id == "design_plan_schema":
            return "schema_design"
        if node_id.startswith("generate_item") or node_id.startswith("generate_day"):
            return "item_generation"
        if node_id in {"verify_coverage", "verify_output"}:
            return "coverage_verification"
        if node.is_final_response():
            return "final_response"
        if node.spec.task_type in {"tool_call", "retrieval"}:
            return "retrieval"
        if node.spec.task_type == "extraction":
            return "extraction"
        if node.spec.task_type == "summarization":
            return "summarization"
        if node.spec.task_type == "comparison":
            return "comparison"
        if node.spec.task_type == "calculation":
            return "calculation"
        return "generic"

    def _preferred_agents_for_role(self, node: TaskNode, role: str) -> list[str]:
        if role == "requirements_analysis":
            return ["reason_agent", "synthesize_agent"]
        if role == "schema_design":
            return ["reason_agent", "verify_agent", "synthesize_agent"]
        if role == "coverage_verification":
            return ["verify_agent", "reason_agent", "synthesize_agent"]
        if role == "quality_verification":
            return ["verify_agent", "reason_agent"]
        if role == "final_response":
            return ["synthesize_agent", "reason_agent"]
        if role == "retrieval":
            return ["retrieve_agent", "extract_agent"]
        if role == "extraction":
            return ["extract_agent", "reason_agent"]
        if role == "summarization":
            return ["summarize_agent", "reason_agent", "synthesize_agent"]
        if role == "comparison":
            return ["compare_agent", "reason_agent"]
        if role == "calculation":
            return ["calculate_agent", "reason_agent"]
        if role == "item_generation":
            task_type_map = {
                "summarization": ["summarize_agent", "reason_agent"],
                "comparison": ["compare_agent", "reason_agent"],
                "calculation": ["calculate_agent", "reason_agent"],
                "extraction": ["extract_agent", "reason_agent"],
                "aggregation": ["synthesize_agent", "reason_agent"],
            }
            return task_type_map.get(node.spec.task_type, ["synthesize_agent", "reason_agent"])
        return ["reason_agent", "synthesize_agent"]

    def _operator_bonus(self, spec: AgentSpec, node: TaskNode) -> float:
        operator = str(node.metadata.get("operator", "")).strip().lower()
        if not operator:
            return 0.0
        if operator.startswith("retrieve") and "retrieve" in spec.capabilities:
            return self.operator_preference_bonus
        if operator.startswith("extract") and "extract" in spec.capabilities:
            return self.operator_preference_bonus
        if operator.startswith("summar") and "summarize" in spec.capabilities:
            return self.operator_preference_bonus
        if operator.startswith("compare") and "compare" in spec.capabilities:
            return self.operator_preference_bonus
        if operator.startswith("calculate") and "calculate" in spec.capabilities:
            return self.operator_preference_bonus
        if operator in {"finalize", "generate_item"} and "synthesize" in spec.capabilities:
            return self.operator_preference_bonus
        if operator.startswith("verify") and "verify" in spec.capabilities:
            return self.operator_preference_bonus
        if operator.startswith("schema") and "reason" in spec.capabilities:
            return self.operator_preference_bonus
        return 0.0

    def _score_for_node(self, spec: AgentSpec, node: TaskNode) -> float:
        base = self._score(spec)
        intent_bonus = 0.0
        if node.spec.intent_tags:
            overlap = set(node.spec.intent_tags).intersection(set(spec.supported_intent_tags))
            if overlap:
                intent_bonus += self.intent_match_weight * (len(overlap) / max(len(node.spec.intent_tags), 1))
        hist_bonus = spec.historical_success_by_capability.get(node.spec.capability_tag, spec.reliability * 0.6)
        task_type_bonus = self.task_type_weight if (not spec.task_types or node.spec.task_type in spec.task_types) else 0.0
        output_mode_bonus = self.output_mode_weight if (not spec.output_modes or node.spec.output_mode in spec.output_modes) else 0.0
        final_bonus = self.final_response_weight if node.is_final_response() and ("final_response" in spec.task_types or "synthesize" in spec.capabilities) else 0.0
        role = self._resolve_node_role(node)
        preferred = self._preferred_agents_for_role(node=node, role=role)
        role_bonus = 0.0
        if spec.name in preferred and preferred:
            rank = preferred.index(spec.name)
            role_bonus = self.role_preference_bonus * (1.0 - rank / max(len(preferred), 1))
        operator_bonus = self._operator_bonus(spec=spec, node=node)
        return (
            base
            + intent_bonus
            + hist_bonus
            + task_type_bonus
            + output_mode_bonus
            + final_bonus
            + role_bonus
            + operator_bonus
        )

    def assign(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        return self.assign_by_capability(tree=tree, registry=registry)

    def assign_with_intent(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
        return self.assign_by_capability(tree=tree, registry=registry)

    def assign_by_capability(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree:
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
                key=lambda spec: self._score_for_node(spec, node),
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
                key=lambda spec: self._score_for_node(spec, node),
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
