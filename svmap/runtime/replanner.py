from __future__ import annotations

from abc import ABC, abstractmethod
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
            evidence_node = self._build_evidence_node(target=target, context=context, tree=tree)
            if evidence_node.id not in tree.nodes:
                tree.nodes[evidence_node.id] = evidence_node
            if evidence_node.id not in target.dependencies:
                target.dependencies.append(evidence_node.id)
            target.status = "pending"
            tree.version += 1
            tree.validate()
            return tree

        if decision.action == "abort":
            tree.mark_skipped_subtree(decision.target_node_id)
        return tree

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
