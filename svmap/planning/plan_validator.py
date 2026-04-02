from __future__ import annotations

from typing import List

from svmap.agents.registry import AgentRegistry
from svmap.models.constraints import ConsistencyConstraint
from svmap.models import TaskTree


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
        return errors
