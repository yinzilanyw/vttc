from __future__ import annotations

from typing import List

from svmap.agents.registry import AgentRegistry
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
        return errors
