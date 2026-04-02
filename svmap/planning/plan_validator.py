from __future__ import annotations

from typing import List

from svmap.agents.registry import AgentRegistry
from svmap.models.constraints import ConsistencyConstraint
from svmap.models import TaskNode, TaskTree


class PlanValidator:
    def validate(self, tree: TaskTree, registry: AgentRegistry) -> List[str]:
        errors: List[str] = []
        try:
            tree.topo_sort()
        except Exception as exc:
            errors.append(f"invalid_dag:{exc}")

        for node in tree.nodes.values():
            if not node.spec.io.output_fields:
                errors.append(f"node:{node.id}:missing_output_schema")
            if node.assigned_agent:
                if not registry.has(node.assigned_agent):
                    errors.append(f"node:{node.id}:unknown_agent:{node.assigned_agent}")
            else:
                candidates = registry.find_candidates(node.spec.capability_tag)
                if not candidates:
                    errors.append(
                        f"node:{node.id}:no_agent_for_capability:{node.spec.capability_tag}"
                    )

            for constraint in node.spec.constraints:
                if not isinstance(constraint, ConsistencyConstraint):
                    continue
                for _, upstream_path in constraint.upstream_fields.items():
                    if "." not in upstream_path:
                        errors.append(
                            f"node:{node.id}:invalid_upstream_path:{upstream_path}"
                        )
                        continue
                    upstream_node_id, upstream_field = upstream_path.split(".", 1)
                    upstream_node = tree.nodes.get(upstream_node_id)
                    if upstream_node is None:
                        errors.append(
                            f"node:{node.id}:missing_upstream_node:{upstream_node_id}"
                        )
                        continue
                    upstream_schema_fields = {
                        f.name for f in upstream_node.spec.io.output_fields
                    }
                    if upstream_field not in upstream_schema_fields:
                        errors.append(
                            f"node:{node.id}:missing_upstream_field:{upstream_path}"
                        )
        errors.extend(self.validate_intents(tree))
        errors.extend(self.validate_cross_node_constraints(tree))
        return errors

    def validate_intents(self, tree: TaskTree) -> List[str]:
        errors: List[str] = []
        for node in tree.nodes.values():
            if node.spec.intent is None:
                errors.append(f"node:{node.id}:missing_intent")
                continue
            if not node.spec.intent.goal.strip():
                errors.append(f"node:{node.id}:empty_intent_goal")
        return errors

    def validate_patch(
        self,
        tree: TaskTree,
        patch_nodes: List[TaskNode],
        attach_to: str,
    ) -> List[str]:
        errors: List[str] = []
        if attach_to not in tree.nodes:
            errors.append(f"patch:attach_node_missing:{attach_to}")
        seen_ids = set()
        for node in patch_nodes:
            node_id = getattr(node, "id", "")
            if not node_id:
                errors.append("patch:node_missing_id")
                continue
            if node_id in seen_ids:
                errors.append(f"patch:duplicate_node_id:{node_id}")
            seen_ids.add(node_id)
        return errors

    def validate_subtree_replacement(
        self,
        tree: TaskTree,
        root_node_id: str,
        new_nodes: List[TaskNode],
    ) -> List[str]:
        errors: List[str] = []
        if root_node_id not in tree.nodes:
            errors.append(f"replace_subtree:root_missing:{root_node_id}")
        new_ids = {getattr(node, "id", "") for node in new_nodes}
        if root_node_id and root_node_id not in new_ids:
            errors.append(f"replace_subtree:root_not_in_new_nodes:{root_node_id}")
        return errors

    def validate_cross_node_constraints(self, tree: TaskTree) -> List[str]:
        errors: List[str] = []
        for node in tree.nodes.values():
            for constraint in node.spec.constraints:
                if not isinstance(constraint, ConsistencyConstraint):
                    continue
                for _, upstream_path in constraint.upstream_fields.items():
                    if "." not in upstream_path:
                        errors.append(
                            f"node:{node.id}:consistency_invalid_path:{upstream_path}"
                        )
        return errors
