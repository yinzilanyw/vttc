from __future__ import annotations

from svmap.models import FieldSpec, NodeIO, NodeSpec, TaskNode
from svmap.verification import FinalResponseVerifier


def _make_final_node() -> TaskNode:
    spec = NodeSpec(
        description="Return final answer",
        capability_tag="synthesize",
        task_type="final_response",
        answer_role="final",
        output_mode="text",
        io=NodeIO(output_fields=[FieldSpec(name="answer", field_type="string", required=True)]),
    )
    return TaskNode(id="final_response", spec=spec, dependencies=["n1", "n2"])


def test_final_response_query_echo_should_fail() -> None:
    node = _make_final_node()
    verifier = FinalResponseVerifier()
    output = {"answer": "Design a 7-day learning plan for multi-agent systems.", "used_nodes": ["n1", "n2"]}
    context = {"global_context": {"query": "Design a 7-day learning plan for multi-agent systems."}, "dependency_outputs": {"n1": {}, "n2": {}}}
    results = verifier.verify(node=node, output=output, context=context)
    assert any(r.code == "final_answer_query_echo" for r in results)


def test_final_response_missing_day_structure_should_fail() -> None:
    node = _make_final_node()
    verifier = FinalResponseVerifier()
    output = {"answer": "Day 1: Goal only.", "used_nodes": ["n1", "n2"]}
    context = {"global_context": {"query": "Design a 7-day learning plan with goals, deliverables and metrics."}, "dependency_outputs": {"n1": {}, "n2": {}}}
    results = verifier.verify(node=node, output=output, context=context)
    assert any(r.code == "final_answer_missing_structure" for r in results)


def test_final_response_missing_sections_should_fail() -> None:
    node = _make_final_node()
    verifier = FinalResponseVerifier()
    answer = "\n".join([f"Day {i}: goal=Learn component {i}" for i in range(1, 8)])
    output = {"answer": answer, "used_nodes": ["n1", "n2"]}
    context = {"global_context": {"query": "Design a 7-day learning plan with daily goals deliverables and metrics."}, "dependency_outputs": {"n1": {}, "n2": {}}}
    results = verifier.verify(node=node, output=output, context=context)
    assert any(r.code == "final_answer_missing_structure" for r in results)


def test_final_response_valid_7day_plan_should_pass() -> None:
    node = _make_final_node()
    verifier = FinalResponseVerifier()
    lines = []
    for i in range(1, 8):
        lines.append(
            f"Day {i}: goal=Finish module {i}; deliverable=Demo {i}; metric=Pass checklist {i}."
        )
    output = {"answer": "\n".join(lines), "used_nodes": ["n1", "n2"]}
    context = {"global_context": {"query": "Design a 7-day learning plan with daily goals deliverables and metrics."}, "dependency_outputs": {"n1": {"x": 1}, "n2": {"x": 2}}}
    results = verifier.verify(node=node, output=output, context=context)
    assert results == []

