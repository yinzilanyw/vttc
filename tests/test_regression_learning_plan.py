from __future__ import annotations

from svmap.models import FieldSpec, NodeIO, NodeSpec, TaskNode
from svmap.planning import ConstraintAwarePlanner, PlanningContext
from svmap.verification import CalculationVerifier, FinalResponseVerifier


def test_learning_plan_query_builds_plan_family_tree() -> None:
    query = "Design a 7-day learning plan with daily goals, deliverables, and metrics."
    planner = ConstraintAwarePlanner(llm_planner=None)
    family = planner.infer_task_family(query)
    assert family == "plan"

    tree = planner.plan(
        PlanningContext(
            user_query=query,
            available_agents=["reason_agent", "verify_agent", "synthesize_agent"],
            available_tools=[],
            task_family=family,
        )
    )

    day_nodes = [node_id for node_id in tree.nodes if node_id.startswith("generate_day")]
    assert len(day_nodes) == 7
    assert "final_response" in tree.nodes


def test_learning_plan_final_answer_must_not_echo_query() -> None:
    node = TaskNode(
        id="final_response",
        dependencies=["generate_day1", "verify_coverage"],
        spec=NodeSpec(
            description="Return final 7-day learning plan.",
            capability_tag="synthesize",
            task_type="final_response",
            answer_role="final",
            output_mode="text",
            io=NodeIO(output_fields=[FieldSpec(name="answer", field_type="string", required=True)]),
        ),
    )
    verifier = FinalResponseVerifier()
    query = "Design a 7-day learning plan with daily goals, deliverables, and metrics."
    result = verifier.verify(
        node=node,
        output={"answer": query, "used_nodes": ["generate_day1", "verify_coverage"]},
        context={"global_context": {"query": query}, "dependency_outputs": {"generate_day1": {}, "verify_coverage": {}}},
    )
    assert any(item.code == "final_answer_query_echo" for item in result)


def test_calculation_error_must_trigger_failure() -> None:
    node = TaskNode(
        id="day1_calculate",
        spec=NodeSpec(
            description="Calculate day metric",
            capability_tag="calculate",
            task_type="calculation",
            output_mode="number",
            io=NodeIO(output_fields=[FieldSpec(name="result", field_type="number", required=True)]),
        ),
    )
    verifier = CalculationVerifier()
    result = verifier.verify(
        node=node,
        output={"expression": "2 +", "result": 0, "calculation_error": "invalid syntax"},
        context={},
    )
    assert any(item.failure_type == "internal_execution_error" for item in result)

