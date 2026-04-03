from __future__ import annotations

from svmap.models import FieldSpec, NodeIO, NodeSpec, TaskNode
from svmap.models.constraints import NoInternalErrorConstraint
from svmap.verification import ExtractionVerifier, RetrievalVerifier


def _make_node(task_type: str, answer_role: str = "intermediate") -> TaskNode:
    spec = NodeSpec(
        description=f"{task_type} node",
        capability_tag="reason",
        task_type=task_type,
        answer_role=answer_role,
        output_mode="json",
        io=NodeIO(output_fields=[FieldSpec(name="result", field_type="string", required=False)]),
    )
    return TaskNode(id=f"{task_type}_node", spec=spec)


def test_empty_extraction_should_fail() -> None:
    node = _make_node("extraction")
    verifier = ExtractionVerifier()
    results = verifier.verify(node=node, output={"extracted": {}}, context={})
    assert any(r.code == "empty_extraction" for r in results)


def test_echo_retrieval_should_fail() -> None:
    node = _make_node("tool_call")
    verifier = RetrievalVerifier()
    output = {
        "query": "Who is the CEO of OpenAI?",
        "evidence": "Who is the CEO of OpenAI?",
        "source": "bailian_direct",
    }
    context = {"global_context": {"query": "Who is the CEO of OpenAI?"}}
    results = verifier.verify(node=node, output=output, context=context)
    assert any(r.code == "echo_retrieval" for r in results)


def test_no_internal_error_constraint_should_fail_on_error_field() -> None:
    node = _make_node("calculation")
    constraint = NoInternalErrorConstraint()
    result = constraint.validate(
        node=node,
        output={"result": 0, "calculation_error": "invalid syntax"},
        context={},
    )
    assert result.passed is False
    assert result.code == "internal_execution_error"

