from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Set

from .constraints import ConstraintParser, RequiredFieldsConstraint
from .task_node import ExecutionPolicy, FieldSpec, IntentSpec, NodeIO, NodeSpec, TaskNode


class TaskTree:
    def __init__(self, nodes: Dict[str, TaskNode]) -> None:
        self.nodes = nodes
        self.version = 1
        self.root_ids: List[str] = []
        self.metadata: Dict[str, Any] = {}
        self.replan_history: List[Dict[str, Any]] = []
        self.graph_deltas: List[Dict[str, Any]] = []
        self.validate()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskTree":
        parser = ConstraintParser()
        nodes: Dict[str, TaskNode] = {}
        for node_data in data.get("nodes", []):
            raw_constraints = node_data.get("constraint") or node_data.get("constraints") or []
            constraints = parser.parse(raw_constraints)
            capability_tag = node_data.get("capability_tag") or _infer_capability(
                node_data.get("agent", "")
            )

            io = _parse_or_build_node_io(node_data, constraints)
            raw_intent = node_data.get("intent")
            intent = None
            if isinstance(raw_intent, dict):
                intent = IntentSpec(
                    goal=raw_intent.get("goal", ""),
                    success_conditions=raw_intent.get("success_conditions", []),
                    evidence_requirements=raw_intent.get("evidence_requirements", []),
                    dependency_assumptions=raw_intent.get("dependency_assumptions", []),
                    output_semantics=raw_intent.get("output_semantics", {}),
                )
            spec = NodeSpec(
                description=node_data.get("description", ""),
                capability_tag=capability_tag,
                io=io,
                constraints=constraints,
                intent=intent,
                intent_tags=node_data.get("intent_tags", []),
            )

            fallback_agents = node_data.get("fallback_agents", [])
            fallback_agent = node_data.get("fallback_agent")
            if fallback_agent and fallback_agent not in fallback_agents:
                fallback_agents = [fallback_agent] + list(fallback_agents)

            policy = ExecutionPolicy(max_retry=node_data.get("max_retry", 2))
            node = TaskNode(
                id=node_data["id"],
                spec=spec,
                dependencies=node_data.get("dependencies", []),
                assigned_agent=node_data.get("agent"),
                fallback_agents=fallback_agents,
                inputs=node_data.get("inputs", {}),
                execution_policy=policy,
                metadata=node_data.get("metadata", {}),
                parent_intent_ids=node_data.get("parent_intent_ids", []),
                intent_status=node_data.get("intent_status", "unknown"),
                repair_history=node_data.get("repair_history", []),
            )
            nodes[node.id] = node

        tree = cls(nodes=nodes)
        tree.metadata = data.get("metadata", {})
        return tree

    def validate(self) -> None:
        for node in self.nodes.values():
            for dep in node.dependencies:
                if dep not in self.nodes:
                    raise ValueError(f"Node '{node.id}' has unknown dependency '{dep}'.")
        self.topo_sort()
        self.root_ids = [node_id for node_id, node in self.nodes.items() if not node.dependencies]

    def topo_sort(self) -> List[str]:
        indegree = {node_id: 0 for node_id in self.nodes}
        adjacency: Dict[str, List[str]] = {node_id: [] for node_id in self.nodes}
        for node_id, node in self.nodes.items():
            for dep in node.dependencies:
                indegree[node_id] += 1
                adjacency[dep].append(node_id)

        queue = deque([node_id for node_id, d in indegree.items() if d == 0])
        order: List[str] = []
        while queue:
            current = queue.popleft()
            order.append(current)
            for nxt in adjacency[current]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)

        if len(order) != len(self.nodes):
            raise ValueError("TaskTree has a cycle and is not a valid DAG.")
        return order

    def get_ready_nodes(self) -> List[TaskNode]:
        ready: List[TaskNode] = []
        for node in self.nodes.values():
            if node.status != "pending":
                continue
            if all(self.nodes[dep].status == "success" for dep in node.dependencies):
                ready.append(node)
        return ready

    def get_downstream_nodes(self, node_id: str) -> List[str]:
        adjacency: Dict[str, List[str]] = {nid: [] for nid in self.nodes}
        for nid, node in self.nodes.items():
            for dep in node.dependencies:
                adjacency[dep].append(nid)

        downstream: List[str] = []
        queue = deque(adjacency.get(node_id, []))
        seen: Set[str] = set()
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            downstream.append(current)
            for nxt in adjacency.get(current, []):
                queue.append(nxt)
        return downstream

    def replace_subgraph(self, failed_node_id: str, new_nodes: List[TaskNode]) -> None:
        remove_ids = {failed_node_id, *self.get_downstream_nodes(failed_node_id)}
        before_version = self.version
        for node_id in remove_ids:
            self.nodes.pop(node_id, None)

        for node in new_nodes:
            self.nodes[node.id] = node

        self.version += 1
        self.validate()
        self.record_graph_delta(
            action="replace_subgraph",
            payload={
                "failed_node_id": failed_node_id,
                "removed_ids": sorted(remove_ids),
                "inserted_ids": [node.id for node in new_nodes],
                "before_version": before_version,
                "after_version": self.version,
            },
        )

    def mark_skipped_subtree(self, node_id: str) -> None:
        ids = [node_id, *self.get_downstream_nodes(node_id)]
        for nid in ids:
            if nid in self.nodes and self.nodes[nid].status == "pending":
                self.nodes[nid].status = "skipped"

    def get_subtree(self, node_id: str) -> List[str]:
        return [node_id, *self.get_downstream_nodes(node_id)]

    def remove_subtree(self, node_id: str) -> None:
        subtree = self.get_subtree(node_id)
        before_version = self.version
        for nid in subtree:
            self.nodes.pop(nid, None)
        self.version += 1
        self.validate()
        self.record_graph_delta(
            action="remove_subtree",
            payload={
                "root_node_id": node_id,
                "removed_ids": subtree,
                "before_version": before_version,
                "after_version": self.version,
            },
        )

    def replace_subtree(self, root_node_id: str, new_nodes: List[TaskNode]) -> None:
        subtree = self.get_subtree(root_node_id)
        before_version = self.version
        for nid in subtree:
            self.nodes.pop(nid, None)
        for node in new_nodes:
            self.nodes[node.id] = node
        self.version += 1
        self.validate()
        self.record_graph_delta(
            action="replace_subtree",
            payload={
                "root_node_id": root_node_id,
                "removed_ids": subtree,
                "inserted_ids": [node.id for node in new_nodes],
                "before_version": before_version,
                "after_version": self.version,
            },
        )

    def record_graph_delta(self, action: str, payload: Dict[str, Any]) -> None:
        delta = {"action": action, "payload": payload, "version": self.version}
        self.graph_deltas.append(delta)
        self.replan_history.append(delta)

    def affected_downstream(self, node_id: str) -> List[str]:
        return self.get_downstream_nodes(node_id)


def _infer_capability(agent_name: str) -> str:
    if not agent_name:
        return "reason"
    return agent_name.replace("_agent", "")


def _parse_field_specs(raw_fields: List[Dict[str, Any]]) -> List[FieldSpec]:
    return [
        FieldSpec(
            name=item.get("name", ""),
            field_type=item.get("field_type", "string"),
            required=item.get("required", True),
            description=item.get("description", ""),
        )
        for item in raw_fields
        if item.get("name")
    ]


def _parse_or_build_node_io(node_data: Dict[str, Any], constraints: List[Any]) -> NodeIO:
    raw_io = node_data.get("io", {})
    input_fields = _parse_field_specs(raw_io.get("input_fields", []))
    output_fields = _parse_field_specs(raw_io.get("output_fields", []))

    if output_fields:
        return NodeIO(input_fields=input_fields, output_fields=output_fields)

    inferred_required: List[str] = []
    for c in constraints:
        if isinstance(c, RequiredFieldsConstraint):
            inferred_required.extend(c.fields)

    output_field_names, used_fallback = _infer_output_field_names(
        node_data=node_data,
        required_fields=inferred_required,
    )
    output_fields: List[FieldSpec] = []
    for name in output_field_names:
        required = not (used_fallback and name == "result")
        output_fields.append(FieldSpec(name=name, field_type="string", required=required))
    return NodeIO(input_fields=input_fields, output_fields=output_fields)


def _infer_output_field_names(
    node_data: Dict[str, Any],
    required_fields: List[str],
) -> tuple[List[str], bool]:
    names: List[str] = []
    for item in required_fields:
        text = item.strip()
        if text and text not in names:
            names.append(text)

    agent = str(node_data.get("agent", "")).lower()
    capability = str(node_data.get("capability_tag", "")).lower()
    description = str(node_data.get("description", "")).lower()
    text = " ".join([agent, capability, description])

    heuristic_map = [
        ("search_agent", "founder"),
        ("search", "founder"),
        ("founder", "founder"),
        ("company", "company"),
        ("ceo", "ceo"),
        ("evidence", "evidence"),
        ("source", "source"),
    ]
    for needle, field_name in heuristic_map:
        if needle in text and field_name not in names:
            names.append(field_name)

    used_fallback = False
    if not names:
        names.append("result")
        used_fallback = True
    return names, used_fallback
