
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Optional

from svmap.agents import (
    AgentRegistry,
    AgentSpec,
    CalculateAgent,
    CapabilityBasedAssigner,
    CompareAgent,
    ExtractAgent,
    RetrieveAgent,
    SummarizeAgent,
    SynthesizeAgent,
)
from svmap.models import ExecutionContext, RuntimeBudget
from svmap.planning import (
    BailianSemanticJudge,
    BailianTaskPlanner,
    ConstraintAwarePlanner,
    PlanValidator,
    PlanningContext,
)
from svmap.runtime import ConstraintAwareReplanner, ExecutionRuntime, MetricsCollector, TraceLogger
from svmap.verification import (
    CalculationVerifier,
    ComparisonVerifier,
    CrossNodeGraphVerifier,
    CrossNodeVerifier,
    CustomNodeVerifier,
    FinalResponseVerifier,
    IntentVerifier,
    RuleVerifier,
    SchemaVerifier,
    SemanticVerifier,
    SummarizationVerifier,
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


def build_demo_queries() -> Dict[str, str]:
    return {
        "qa": "Who is the CEO of the company founded by Elon Musk?",
        "summary": "Summarize the key facts about the company founded by Elon Musk.",
        "compare": "Compare SpaceX and OpenAI in one concise answer.",
        "calculate": "Calculate 25 * 4 + 6.",
        "extract": "Extract founder and company from: 'Elon Musk founded SpaceX'.",
    }


def build_default_knowledge_base() -> Dict[str, Dict[str, str]]:
    return {
        "elon musk": {
            "company": "SpaceX",
            "ceo": "Elon Musk",
            "summary": "Elon Musk founded SpaceX and also serves as CEO.",
        },
        "sam altman": {
            "company": "OpenAI",
            "ceo": "Sam Altman",
            "summary": "Sam Altman is the CEO of OpenAI.",
        },
        "mark zuckerberg": {
            "company": "Meta",
            "ceo": "Mark Zuckerberg",
            "summary": "Mark Zuckerberg is founder and CEO of Meta.",
        },
    }


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


def build_multitask_registry(kb: Dict[str, Dict[str, str]]) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        "retrieve_agent",
        RetrieveAgent(kb),
        AgentSpec(
            name="retrieve_agent",
            capabilities=["retrieve", "reason"],
            task_types=["tool_call", "reasoning", "extraction"],
            output_modes=["json", "text", "table"],
            supported_intent_tags=["retrieve", "evidence"],
            repair_specialties=["verification", "planning"],
            historical_success_by_capability={"retrieve": 0.95},
            reliability=0.95,
            cost_weight=1.0,
        ),
    )
    registry.register(
        "extract_agent",
        ExtractAgent(kb),
        AgentSpec(
            name="extract_agent",
            capabilities=["extract", "reason"],
            task_types=["extraction", "reasoning"],
            output_modes=["json", "text", "table"],
            supported_intent_tags=["extract", "field"],
            repair_specialties=["verification"],
            historical_success_by_capability={"extract": 0.92},
            reliability=0.92,
            cost_weight=1.0,
        ),
    )
    registry.register(
        "summarize_agent",
        SummarizeAgent(),
        AgentSpec(
            name="summarize_agent",
            capabilities=["summarize", "reason"],
            task_types=["summarization", "aggregation"],
            output_modes=["text", "json"],
            supported_intent_tags=["summarize"],
            repair_specialties=["runtime", "verification"],
            historical_success_by_capability={"summarize": 0.9},
            reliability=0.9,
            cost_weight=1.0,
        ),
    )
    registry.register(
        "compare_agent",
        CompareAgent(),
        AgentSpec(
            name="compare_agent",
            capabilities=["compare", "reason"],
            task_types=["comparison", "aggregation"],
            output_modes=["table", "json", "text"],
            supported_intent_tags=["compare"],
            repair_specialties=["runtime", "verification"],
            historical_success_by_capability={"compare": 0.88},
            reliability=0.88,
            cost_weight=1.1,
        ),
    )
    registry.register(
        "calculate_agent",
        CalculateAgent(),
        AgentSpec(
            name="calculate_agent",
            capabilities=["calculate", "reason"],
            task_types=["calculation", "reasoning"],
            output_modes=["number", "json", "text"],
            supported_intent_tags=["calculate"],
            repair_specialties=["verification", "runtime"],
            historical_success_by_capability={"calculate": 0.85},
            reliability=0.85,
            cost_weight=1.0,
        ),
    )
    registry.register(
        "synthesize_agent",
        SynthesizeAgent(kb),
        AgentSpec(
            name="synthesize_agent",
            capabilities=["synthesize", "reason"],
            task_types=["final_response", "aggregation", "reasoning"],
            output_modes=["text", "json", "table"],
            supported_intent_tags=["synthesize", "final"],
            repair_specialties=["planning", "verification", "runtime"],
            historical_success_by_capability={"synthesize": 0.95},
            reliability=0.95,
            cost_weight=1.1,
        ),
    )
    registry.register(
        "reason_agent",
        SynthesizeAgent(kb),
        AgentSpec(
            name="reason_agent",
            capabilities=["reason", "synthesize", "extract", "summarize"],
            task_types=["reasoning", "aggregation", "comparison", "calculation", "verification"],
            output_modes=["text", "json", "table", "number", "boolean"],
            supported_intent_tags=["reason"],
            repair_specialties=["runtime", "planning", "verification"],
            historical_success_by_capability={"reason": 0.9},
            reliability=0.9,
            cost_weight=1.0,
        ),
    )
    registry.register(
        "verify_agent",
        SynthesizeAgent(kb),
        AgentSpec(
            name="verify_agent",
            capabilities=["verify", "reason", "synthesize"],
            task_types=["verification", "reasoning", "comparison", "calculation", "final_response"],
            output_modes=["text", "json", "boolean", "table", "number"],
            supported_intent_tags=["verify"],
            repair_specialties=["verification", "planning"],
            historical_success_by_capability={"verify": 0.88, "reason": 0.86},
            reliability=0.88,
            cost_weight=1.0,
        ),
    )

    # Backward-compatible aliases for older configs.
    base_retrieve = registry.get_spec("retrieve_agent")
    base_extract = registry.get_spec("extract_agent")
    base_synthesize = registry.get_spec("synthesize_agent")
    registry.register(
        "search_agent",
        RetrieveAgent(kb),
        AgentSpec(
            name="search_agent",
            capabilities=list(base_retrieve.capabilities),
            task_types=list(base_retrieve.task_types),
            output_modes=list(base_retrieve.output_modes),
            supported_intent_tags=list(base_retrieve.supported_intent_tags),
            repair_specialties=list(base_retrieve.repair_specialties),
            historical_success_by_capability=dict(base_retrieve.historical_success_by_capability),
            reliability=base_retrieve.reliability,
            cost_weight=base_retrieve.cost_weight,
            latency_weight=base_retrieve.latency_weight,
        ),
    )
    registry.register(
        "company_agent",
        ExtractAgent(kb),
        AgentSpec(
            name="company_agent",
            capabilities=list(base_extract.capabilities),
            task_types=list(base_extract.task_types),
            output_modes=list(base_extract.output_modes),
            supported_intent_tags=list(base_extract.supported_intent_tags),
            repair_specialties=list(base_extract.repair_specialties),
            historical_success_by_capability=dict(base_extract.historical_success_by_capability),
            reliability=base_extract.reliability,
            cost_weight=base_extract.cost_weight,
            latency_weight=base_extract.latency_weight,
        ),
    )
    registry.register(
        "ceo_agent",
        ExtractAgent(kb),
        AgentSpec(
            name="ceo_agent",
            capabilities=list(base_extract.capabilities),
            task_types=list(base_extract.task_types),
            output_modes=list(base_extract.output_modes),
            supported_intent_tags=list(base_extract.supported_intent_tags),
            repair_specialties=list(base_extract.repair_specialties),
            historical_success_by_capability=dict(base_extract.historical_success_by_capability),
            reliability=base_extract.reliability,
            cost_weight=base_extract.cost_weight,
            latency_weight=base_extract.latency_weight,
        ),
    )
    registry.register(
        "ceo_fallback_agent",
        SynthesizeAgent(kb),
        AgentSpec(
            name="ceo_fallback_agent",
            capabilities=list(base_synthesize.capabilities),
            task_types=list(base_synthesize.task_types),
            output_modes=list(base_synthesize.output_modes),
            supported_intent_tags=list(base_synthesize.supported_intent_tags),
            repair_specialties=list(base_synthesize.repair_specialties),
            historical_success_by_capability=dict(base_synthesize.historical_success_by_capability),
            reliability=base_synthesize.reliability,
            cost_weight=base_synthesize.cost_weight,
            latency_weight=base_synthesize.latency_weight,
        ),
    )
    return registry


def _extract_final_answer(result: Dict[str, Any]) -> str:
    report = result["report"]
    if report.final_output and isinstance(report.final_output, dict):
        answer = report.final_output.get("answer") or report.final_output.get("final_response")
        if isinstance(answer, str) and answer.strip():
            return answer.strip()

    for node_id in reversed(result["dag_order"]):
        record = report.node_records.get(node_id)
        if not record or not isinstance(record.output, dict):
            continue
        output = record.output
        for key in ("answer", "final_response", "summary", "comparison", "result", "ceo", "company"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)):
                return str(value)
    return ""


def run_demo_collect(
    query: Optional[str] = None,
    task_family: Optional[str] = None,
    stop_on_failure: Optional[bool] = None,
    export_trace: bool = True,
) -> Dict[str, Any]:
    start_ts = time.time()
    load_env_file(".env")

    planner_for_family = ConstraintAwarePlanner(llm_planner=None)
    family = task_family or os.getenv("DEMO_TASK_FAMILY", "").strip().lower() or "qa"
    demos = build_demo_queries()
    if query:
        user_query = query
    elif task_family and family in demos:
        user_query = demos[family]
    else:
        user_query = os.getenv("DEMO_QUERY") or demos.get(family) or DEFAULT_QUERY
    user_query = user_query.strip() or DEFAULT_QUERY
    if not task_family and family not in demos:
        family = planner_for_family.infer_task_family(user_query)

    components = build_online_components_from_env()
    planner = ConstraintAwarePlanner(llm_planner=components["planner"])

    kb = build_default_knowledge_base()
    registry = build_multitask_registry(kb)

    planning_context = PlanningContext(
        user_query=user_query,
        available_agents=registry.names(),
        available_tools=[],
        global_goal="Solve multi-task user query with structurally verifiable task DAG.",
        replan_scope="none",
        task_family=family,
    )
    task_tree = planner.plan(planning_context)
    task_tree.ensure_single_final_response()

    assignment_mode = os.getenv("ASSIGNMENT_MODE", "capability").strip().lower()
    if assignment_mode == "naive":
        for node in task_tree.nodes.values():
            if node.is_final_response():
                node.assigned_agent = "synthesize_agent"
            else:
                node.assigned_agent = "reason_agent"
            node.fallback_agents = []
    else:
        assigner = CapabilityBasedAssigner()
        task_tree = assigner.assign_by_capability(task_tree, registry)

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
            SummarizationVerifier(),
            ComparisonVerifier(),
            CalculationVerifier(),
            FinalResponseVerifier(),
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

    if export_trace:
        os.makedirs("artifacts", exist_ok=True)
        trace_path = os.path.join("artifacts", f"trace_{int(time.time())}.json")
        trace_logger.export_json(trace_path)
        report.trace_path = trace_path

    metrics = MetricsCollector().summarize(report)
    dag_order = task_tree.topo_sort()
    elapsed_sec = time.time() - start_ts

    result = {
        "mode": components["mode"],
        "task_family": family,
        "query": user_query,
        "dag_order": dag_order,
        "success": report.success,
        "total_retries": report.total_retries,
        "verification_failures": report.verification_failures,
        "replan_count": report.replan_count,
        "plan_versions": report.plan_versions,
        "trace_path": report.trace_path,
        "budget_exhausted": report.budget_exhausted,
        "elapsed_sec": elapsed_sec,
        "metrics": metrics.__dict__,
        "report": report,
        "task_tree": task_tree,
    }
    result["final_answer"] = _extract_final_answer(result)
    return result


def run_demo_query(task_family: str, query: str) -> None:
    result = run_demo_collect(query=query, task_family=task_family)
    report = result["report"]
    metrics = result["metrics"]

    print("=== Structured Verifiable Multi-Agent Planning (Modular MVP) ===")
    print("Mode:", result["mode"])
    print("Task family:", result["task_family"])
    print("Query:", result["query"])
    print("DAG order:", " -> ".join(result["dag_order"]))
    print("Success:", report.success)
    print("Final answer:", result.get("final_answer", ""))
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
        print(
            f"[{node_id}] status={rec.status}, attempts={rec.attempts}, "
            f"agent={rec.agent_used}, task_type={rec.task_type}"
        )
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
    print(f" final_response_success_rate={metrics['final_response_success_rate']:.2f}")
    print(f" multitask_generalization_score={metrics['multitask_generalization_score']:.2f}")


def run_demo(task_family: Optional[str] = None, query: Optional[str] = None) -> None:
    load_env_file(".env")
    demos = build_demo_queries()
    family = (task_family or os.getenv("DEMO_TASK_FAMILY", "qa")).strip().lower() or "qa"

    if family == "all":
        for item_family, item_query in demos.items():
            print(f"\n### Demo Family: {item_family} ###")
            run_demo_query(task_family=item_family, query=item_query)
        return

    if query:
        selected_query = query
    elif task_family and family in demos:
        selected_query = demos[family]
    else:
        selected_query = os.getenv("DEMO_QUERY") or demos.get(family, DEFAULT_QUERY)
    run_demo_query(task_family=family, query=selected_query)
