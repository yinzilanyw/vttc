from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Optional

from svmap.models import (
    ConstraintParser,
    ExecutionContext,
    ExecutionPolicy,
    FieldSpec,
    NodeIO,
    NodeFailure,
    NodeSpec,
    TaskNode,
    TaskTree,
)


@dataclass
class ReplanDecision:
    action: str
    target_node_id: str
    patch: Optional[Dict[str, Any]] = None
    reason: str = ""


class BaseReplanner(ABC):
    @abstractmethod
    def decide(
        self,
        node: TaskNode,
        failure: NodeFailure,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> ReplanDecision:
        raise NotImplementedError

    @abstractmethod
    def apply(
        self,
        decision: ReplanDecision,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> TaskTree:
        raise NotImplementedError


class ConstraintAwareReplanner(BaseReplanner):
    def decide(
        self,
        node: TaskNode,
        failure: NodeFailure,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> ReplanDecision:
        reasons_text = " ".join(failure.reasons).lower()
        replan_attempts = int(node.metadata.get("replan_attempts", 0))
        has_evidence_dep = any(dep.startswith("ev_") for dep in node.dependencies)

        if (
            failure.retryable
            and not has_evidence_dep
            and replan_attempts < 2
            and (
                "semantic_check_failed" in reasons_text
                or "missing_source" in reasons_text
                or "factual" in reasons_text
            )
        ):
            return ReplanDecision(
                action="patch_subgraph",
                target_node_id=node.id,
                patch={"insert": "evidence_retrieval"},
                reason="factuality-related failure",
            )

        if (
            failure.retryable
            and node.fallback_agents
            and "semantic_check_failed" not in reasons_text
            and "factual" not in reasons_text
        ):
            return ReplanDecision(
                action="switch_agent",
                target_node_id=node.id,
                reason="retryable non-semantic failure with available fallback agents",
            )

        if failure.retryable and replan_attempts < 3:
            return ReplanDecision(
                action="retry_same",
                target_node_id=node.id,
                reason="default retry",
            )

        return ReplanDecision(action="abort", target_node_id=node.id, reason="not retryable")

    def apply(
        self,
        decision: ReplanDecision,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> TaskTree:
        target = tree.nodes.get(decision.target_node_id)
        if target is None:
            return tree

        target.metadata["replan_attempts"] = int(target.metadata.get("replan_attempts", 0)) + 1

        if decision.action == "retry_same":
            target.status = "pending"
            return tree

        if decision.action == "switch_agent":
            if target.fallback_agents:
                next_agent = target.fallback_agents.pop(0)
                if target.assigned_agent:
                    target.fallback_agents.append(target.assigned_agent)
                target.assigned_agent = next_agent
                target.status = "pending"
            return tree

        if decision.action == "patch_subgraph":
            self._apply_patch_subgraph(target=target, tree=tree, context=context)
            return tree

        if decision.action == "abort":
            tree.mark_skipped_subtree(decision.target_node_id)
        return tree

    def _apply_patch_subgraph(
        self,
        target: TaskNode,
        tree: TaskTree,
        context: ExecutionContext,
    ) -> None:
        downstream_ids = tree.get_downstream_nodes(target.id)
        removed_ids = {target.id, *downstream_ids}

        evidence_node = self._build_evidence_node(target=target, context=context, tree=tree)
        patched_target = deepcopy(target)
        patched_target.status = "pending"
        patched_target.outputs = {}
        patched_target.dependencies = list(target.dependencies)
        if evidence_node.id not in patched_target.dependencies:
            patched_target.dependencies.append(evidence_node.id)

        regenerated_nodes = [evidence_node, patched_target]
        for node_id in downstream_ids:
            if node_id not in tree.nodes:
                continue
            cloned = deepcopy(tree.nodes[node_id])
            cloned.status = "pending"
            cloned.outputs = {}
            regenerated_nodes.append(cloned)

        tree.replace_subgraph(failed_node_id=target.id, new_nodes=regenerated_nodes)

        # Drop stale outputs for removed subtree so runtime recomputes them.
        for node_id in removed_ids:
            context.node_outputs.pop(node_id, None)

    def _build_evidence_node(
        self,
        target: TaskNode,
        context: ExecutionContext,
        tree: TaskTree,
    ) -> TaskNode:
        evidence_id = f"ev_{target.id}_v{tree.version + 1}"
        parser = ConstraintParser()
        constraints = parser.parse(["required_keys:evidence", "non_empty_values"])

        spec = NodeSpec(
            description=f"Retrieve evidence for node {target.id}",
            capability_tag="search",
            io=NodeIO(
                input_fields=[
                    FieldSpec(name="query", field_type="string", required=True),
                ],
                output_fields=[
                    FieldSpec(name="evidence", field_type="string", required=True),
                    FieldSpec(name="source", field_type="string", required=False),
                ],
            ),
            constraints=constraints,
        )
        return TaskNode(
            id=evidence_id,
            spec=spec,
            dependencies=list(target.dependencies),
            assigned_agent="search_agent",
            fallback_agents=[],
            status="pending",
            inputs={"query": context.global_context.get("query", "")},
            execution_policy=ExecutionPolicy(max_retry=1, retryable=True),
            metadata={"generated_by": "constraint_aware_replanner"},
        )
