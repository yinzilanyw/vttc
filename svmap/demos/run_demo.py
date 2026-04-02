from __future__ import annotations

import os
from typing import Any, Dict

from svmap.agents import (
    AgentRegistry,
    AgentSpec,
    CapabilityBasedAssigner,
    CEOAgent,
    CompanyAgent,
    FallbackCEOAgent,
    SearchAgent,
)
from svmap.models import ConstraintResult, ExecutionContext, TaskNode
from svmap.planning import (
    BailianSemanticJudge,
    BailianTaskPlanner,
    ConstraintAwarePlanner,
    PlanValidator,
    PlanningContext,
)
from svmap.runtime import ConstraintAwareReplanner, ExecutionRuntime, MetricsCollector, TraceLogger
from svmap.verification import CustomNodeVerifier, RuleVerifier, SchemaVerifier, SemanticVerifier, VerifierEngine


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and (
                (value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")
            ):
                value = value[1:-1]
            os.environ.setdefault(key, value)


def build_default_knowledge_base() -> Dict[str, Dict[str, str]]:
    return {
        "elon musk": {"company": "SpaceX", "ceo": "Elon Musk"},
        "sam altman": {"company": "OpenAI", "ceo": "Sam Altman"},
        "mark zuckerberg": {"company": "Meta", "ceo": "Mark Zuckerberg"},
    }


def ceo_node_custom_verifier(
    node: TaskNode,
    output: Dict[str, Any],
    context: Dict[str, Any],
) -> ConstraintResult:
    if output.get("company") and output.get("ceo"):
        return ConstraintResult(
            passed=True,
            code="custom_ceo_ok",
            message="company/ceo pair is complete.",
        )
    return ConstraintResult(
        passed=False,
        code="custom_ceo_incomplete",
        message="company/ceo pair incomplete.",
    )


def build_online_components_from_env() -> Dict[str, Any]:
    use_online = os.getenv("USE_MODEL_API", "1").strip().lower() in {"1", "true", "yes", "on"}
    if not use_online:
        return {"planner": None, "semantic_judge": None, "mode": "offline"}

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Bailian mode requires DASHSCOPE_API_KEY.")

    base_url = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    planner_model = os.getenv("PLANNER_MODEL") or os.getenv("BAILIAN_PLANNER_MODEL") or "qwen-plus"
    judge_model = os.getenv("JUDGE_MODEL") or os.getenv("BAILIAN_JUDGE_MODEL") or "qwen-flash"
    return {
        "planner": BailianTaskPlanner(api_key=api_key, base_url=base_url, model=planner_model),
        "semantic_judge": BailianSemanticJudge(
            api_key=api_key,
            base_url=base_url,
            model=judge_model,
        ),
        "mode": f"bailian(planner={planner_model}, judge={judge_model}, base_url={base_url})",
    }


def build_registry(kb: Dict[str, Dict[str, str]]) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        "search_agent",
        SearchAgent(kb),
        AgentSpec(name="search_agent", capabilities=["search"], reliability=0.95, cost_weight=1.0),
    )
    registry.register(
        "company_agent",
        CompanyAgent(kb),
        AgentSpec(name="company_agent", capabilities=["lookup"], reliability=0.95, cost_weight=1.0),
    )
    registry.register(
        "ceo_agent",
        CEOAgent(kb),
        AgentSpec(name="ceo_agent", capabilities=["reason"], reliability=0.9, cost_weight=1.0),
    )
    registry.register(
        "ceo_fallback_agent",
        FallbackCEOAgent(kb),
        AgentSpec(
            name="ceo_fallback_agent",
            capabilities=["reason", "lookup"],
            reliability=0.99,
            cost_weight=1.2,
        ),
    )
    return registry


def run_demo() -> None:
    load_env_file(".env")
    user_query = os.getenv(
        "DEMO_QUERY",
        "Who is the CEO of the company founded by Elon Musk?",
    )
    components = build_online_components_from_env()

    planner = ConstraintAwarePlanner(llm_planner=components["planner"])
    planning_context = PlanningContext(
        user_query=user_query,
        available_agents=["search_agent", "company_agent", "ceo_agent", "ceo_fallback_agent"],
        available_tools=[],
    )
    task_tree = planner.plan(planning_context)

    kb = build_default_knowledge_base()
    registry = build_registry(kb)

    assigner = CapabilityBasedAssigner()
    task_tree = assigner.assign(task_tree, registry)

    # Keep current MVP behavior: n3 has a custom executable verifier.
    if "n3" in task_tree.nodes:
        task_tree.nodes["n3"].metadata["custom_verifier"] = ceo_node_custom_verifier

    validator = PlanValidator()
    plan_errors = validator.validate(task_tree, registry)
    if plan_errors:
        raise RuntimeError(f"Plan validation failed: {plan_errors}")

    semantic_judge = components["semantic_judge"]
    verifier_engine = VerifierEngine(
        verifiers=[
            SchemaVerifier(),
            RuleVerifier(),
            SemanticVerifier(semantic_judge=semantic_judge),
            CustomNodeVerifier(),
        ]
    )

    trace_logger = TraceLogger()
    stop_on_failure = os.getenv("STOP_ON_FAILURE", "0").strip().lower() in {"1", "true", "yes", "on"}
    runtime = ExecutionRuntime(
        registry=registry,
        verifier_engine=verifier_engine,
        replanner=ConstraintAwareReplanner(),
        trace_logger=trace_logger,
        stop_on_failure=stop_on_failure,
    )
    report = runtime.execute(
        tree=task_tree,
        context=ExecutionContext(global_context={"query": user_query}),
    )

    metrics = MetricsCollector().summarize(report)

    print("=== Structured Verifiable Multi-Agent Planning (Modular MVP) ===")
    print("Mode:", components["mode"])
    print("Query:", user_query)
    print("DAG order:", " -> ".join(task_tree.topo_sort()))
    print("Success:", report.success)
    print("Total retries:", report.total_retries)
    print("Verification failures detected:", report.verification_failures)
    print("Replan count:", report.replan_count)
    print("Plan versions:", report.plan_versions)
    print()

    for node_id in task_tree.topo_sort():
        rec = report.node_records.get(node_id)
        if rec is None:
            print(f"[{node_id}] status=not_executed")
            print()
            continue
        print(f"[{node_id}] status={rec.status}, attempts={rec.attempts}, agent={rec.agent_used}")
        print(" output:", rec.output)
        if rec.verify_errors:
            print(" verify_errors:", rec.verify_errors)
        print()

    print("Metrics:")
    print(f" task_success={metrics.task_success}")
    print(f" node_success_rate={metrics.node_success_rate:.2f}")
    print(f" verification_failure_count={metrics.verification_failure_count}")
    print(f" retry_count={metrics.retry_count}")
    print(f" replan_count={metrics.replan_count}")
    print(f" avg_attempts_per_node={metrics.avg_attempts_per_node:.2f}")
