from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Optional

from svmap.agents import (
    AgentRegistry,
    AgentSpec,
    CapabilityBasedAssigner,
    CEOAgent,
    CompanyAgent,
    FallbackCEOAgent,
    SearchAgent,
)
from svmap.models import ConstraintResult, ExecutionContext, RuntimeBudget, TaskNode
from svmap.planning import (
    BailianSemanticJudge,
    BailianTaskPlanner,
    ConstraintAwarePlanner,
    PlanValidator,
    PlanningContext,
)
from svmap.runtime import ConstraintAwareReplanner, ExecutionRuntime, MetricsCollector, TraceLogger
from svmap.verification import (
    CrossNodeVerifier,
    CrossNodeGraphVerifier,
    CustomNodeVerifier,
    IntentVerifier,
    RuleVerifier,
    SchemaVerifier,
    SemanticVerifier,
    VerifierEngine,
)


DEFAULT_QUERY = "Who is the CEO of the company founded by Elon Musk?"


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


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    use_online = _env_flag("USE_MODEL_API", default=True)
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
        AgentSpec(
            name="search_agent",
            capabilities=["search"],
            supported_intent_tags=["search", "evidence"],
            repair_specialties=["verification", "planning"],
            historical_success_by_capability={"search": 0.95},
            reliability=0.95,
            cost_weight=1.0,
        ),
    )
    registry.register(
        "company_agent",
        CompanyAgent(kb),
        AgentSpec(
            name="company_agent",
            capabilities=["lookup"],
            supported_intent_tags=["lookup"],
            repair_specialties=["verification"],
            historical_success_by_capability={"lookup": 0.95},
            reliability=0.95,
            cost_weight=1.0,
        ),
    )
    registry.register(
        "ceo_agent",
        CEOAgent(kb),
        AgentSpec(
            name="ceo_agent",
            capabilities=["reason"],
            supported_intent_tags=["reason"],
            repair_specialties=["runtime", "verification"],
            historical_success_by_capability={"reason": 0.9},
            reliability=0.9,
            cost_weight=1.0,
        ),
    )
    registry.register(
        "ceo_fallback_agent",
        FallbackCEOAgent(kb),
        AgentSpec(
            name="ceo_fallback_agent",
            capabilities=["reason", "lookup"],
            supported_intent_tags=["reason", "lookup"],
            repair_specialties=["verification", "runtime", "planning"],
            historical_success_by_capability={"reason": 0.99, "lookup": 0.97},
            reliability=0.99,
            cost_weight=1.2,
        ),
    )
    return registry


def _extract_final_answer(report: Any, dag_order: list[str]) -> str:
    for node_id in reversed(dag_order):
        record = report.node_records.get(node_id)
        if not record or not isinstance(record.output, dict):
            continue
        output = record.output
        for key in ("answer", "ceo", "result", "output", "company", "founder"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def run_demo_collect(
    query: Optional[str] = None,
    stop_on_failure: Optional[bool] = None,
    export_trace: bool = True,
) -> Dict[str, Any]:
    start_ts = time.time()
    load_env_file(".env")
    user_query = (query or os.getenv("DEMO_QUERY", DEFAULT_QUERY)).strip() or DEFAULT_QUERY
    components = build_online_components_from_env()

    planner = ConstraintAwarePlanner(llm_planner=components["planner"])
    planning_context = PlanningContext(
        user_query=user_query,
        available_agents=["search_agent", "company_agent", "ceo_agent", "ceo_fallback_agent"],
        available_tools=[],
        global_goal="Answer the user query with structurally verifiable multi-hop reasoning.",
        replan_scope="none",
    )
    task_tree = planner.plan(planning_context)

    kb = build_default_knowledge_base()
    registry = build_registry(kb)

    assigner = CapabilityBasedAssigner()
    task_tree = assigner.assign_with_intent(task_tree, registry)

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
            CrossNodeVerifier(),
            CrossNodeGraphVerifier(),
            IntentVerifier(),
            CustomNodeVerifier(),
        ]
    )

    trace_logger = TraceLogger()
    stop_on_failure_flag = _env_flag("STOP_ON_FAILURE", default=False)
    if stop_on_failure is not None:
        stop_on_failure_flag = stop_on_failure
    runtime = ExecutionRuntime(
        registry=registry,
        verifier_engine=verifier_engine,
        replanner=ConstraintAwareReplanner(planner=planner),
        trace_logger=trace_logger,
        stop_on_failure=stop_on_failure_flag,
        parallel=False,
        max_runtime_steps=200,
        budget=RuntimeBudget(max_runtime_steps=200, max_total_attempts=40, max_total_replans=10),
    )
    report = runtime.execute(
        tree=task_tree,
        context=ExecutionContext(
            global_context={"query": user_query},
            trace_id=str(uuid.uuid4()),
        ),
    )
    trace_path: Optional[str] = None
    if export_trace:
        os.makedirs("artifacts", exist_ok=True)
        trace_path = os.path.join("artifacts", f"trace_{int(time.time())}.json")
        trace_logger.export_json(trace_path)
        report.trace_path = trace_path

    metrics = MetricsCollector().summarize(report)
    dag_order = task_tree.topo_sort()
    elapsed_sec = time.time() - start_ts

    return {
        "mode": components["mode"],
        "query": user_query,
        "dag_order": dag_order,
        "success": report.success,
        "total_retries": report.total_retries,
        "verification_failures": report.verification_failures,
        "replan_count": report.replan_count,
        "plan_versions": report.plan_versions,
        "trace_path": report.trace_path,
        "budget_exhausted": report.budget_exhausted,
        "final_answer": _extract_final_answer(report=report, dag_order=dag_order),
        "elapsed_sec": elapsed_sec,
        "metrics": {
            "task_success": metrics.task_success,
            "task_success_rate": metrics.task_success_rate,
            "node_success_rate": metrics.node_success_rate,
            "verification_failure_count": metrics.verification_failure_count,
            "retry_count": metrics.retry_count,
            "replan_count": metrics.replan_count,
            "avg_attempts_per_node": metrics.avg_attempts_per_node,
            "avg_saved_downstream_nodes": metrics.avg_saved_downstream_nodes,
            "parallelizable_node_ratio": metrics.parallelizable_node_ratio,
            "avg_cost_saved_vs_full_rerun": metrics.avg_cost_saved_vs_full_rerun,
        },
        "report": report,
        "task_tree": task_tree,
    }


def run_demo() -> None:
    result = run_demo_collect()
    report = result["report"]
    task_tree = result["task_tree"]
    metrics = result["metrics"]
    print("=== Structured Verifiable Multi-Agent Planning (Modular MVP) ===")
    print("Mode:", result["mode"])
    print("Query:", result["query"])
    print("DAG order:", " -> ".join(result["dag_order"]))
    print("Success:", report.success)
    print("Total retries:", report.total_retries)
    print("Verification failures detected:", report.verification_failures)
    print("Replan count:", report.replan_count)
    print("Plan versions:", report.plan_versions)
    print("Trace path:", report.trace_path)
    print()

    for node_id in result["dag_order"]:
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
    print(f" task_success={metrics['task_success']}")
    print(f" node_success_rate={metrics['node_success_rate']:.2f}")
    print(f" verification_failure_count={metrics['verification_failure_count']}")
    print(f" retry_count={metrics['retry_count']}")
    print(f" replan_count={metrics['replan_count']}")
    print(f" avg_attempts_per_node={metrics['avg_attempts_per_node']:.2f}")
