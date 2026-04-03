from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from svmap.agents import (
    AgentRegistry,
    AgentSpec,
    CalculateAgent,
    CapabilityBasedAssigner,
    CompareAgent,
    ExtractAgent,
    ReasonAgent,
    RetrieveAgent,
    SummarizeAgent,
    SynthesizeAgent,
    VerifyAgent,
)
from svmap.config import AppConfig, load_app_config_from_env
from svmap.models import ExecutionContext, RuntimeBudget, TaskTree
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
    EdgeConsistencyVerifier,
    ExtractionVerifier,
    FinalResponseVerifier,
    IntentVerifier,
    NoPlaceholderVerifier,
    PlanCoverageVerifier,
    PlanSchemaVerifier,
    RequirementsAnalysisVerifier,
    RetrievalVerifier,
    RuleVerifier,
    SchemaVerifier,
    SemanticVerifier,
    SubtreeIntentVerifier,
    SummarizationVerifier,
    VerifierEngine,
)


DEFAULT_QUERY = "Who is the CEO of the company founded by Elon Musk?"


@dataclass
class RunConfig:
    mode: str = "demo"
    task_family: Optional[str] = None
    query: Optional[str] = None
    use_env: bool = True
    export_trace: bool = True
    parallel: bool = False
    stop_on_failure: Optional[bool] = None
    enable_replan: bool = True
    enable_intent_verifier: bool = True
    assignment_mode: Optional[str] = None
    max_runtime_steps: int = 200
    max_total_attempts: int = 40
    max_total_replans: int = 10


@dataclass
class RunResult:
    query: str
    task_family: str
    success: bool
    final_output: Dict[str, Any]
    report: Any
    metrics: Dict[str, Any]
    trace_path: Optional[str] = None
    mode: str = ""
    dag_order: List[str] = field(default_factory=list)
    total_retries: int = 0
    verification_failures: int = 0
    replan_count: int = 0
    plan_versions: int = 1
    budget_exhausted: bool = False
    elapsed_sec: float = 0.0
    task_tree: Optional[TaskTree] = None

    def final_answer(self) -> str:
        answer = self.final_output.get("answer") or self.final_output.get("final_response")
        if isinstance(answer, str):
            return answer.strip()
        if answer is None:
            return ""
        return str(answer)

    def to_legacy_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "task_family": self.task_family,
            "query": self.query,
            "dag_order": self.dag_order,
            "success": self.success,
            "total_retries": self.total_retries,
            "verification_failures": self.verification_failures,
            "replan_count": self.replan_count,
            "plan_versions": self.plan_versions,
            "trace_path": self.trace_path,
            "budget_exhausted": self.budget_exhausted,
            "elapsed_sec": self.elapsed_sec,
            "metrics": self.metrics,
            "report": self.report,
            "task_tree": self.task_tree,
            "final_output": self.final_output,
            "final_answer": self.final_answer(),
        }


def build_online_components(app_config: AppConfig) -> Dict[str, Any]:
    if not app_config.use_model_api:
        return {"planner": None, "semantic_judge": None, "mode": "offline"}

    if not app_config.api_key:
        raise RuntimeError("Bailian mode requires DASHSCOPE_API_KEY.")

    return {
        "planner": BailianTaskPlanner(
            api_key=app_config.api_key,
            base_url=app_config.base_url,
            model=app_config.planner_model,
        ),
        "semantic_judge": BailianSemanticJudge(
            api_key=app_config.api_key,
            base_url=app_config.base_url,
            model=app_config.judge_model,
        ),
        "mode": (
            f"bailian(planner={app_config.planner_model}, "
            f"judge={app_config.judge_model}, base_url={app_config.base_url})"
        ),
    }


def _build_retrieve_agent_kwargs(app_config: AppConfig) -> Dict[str, Any]:
    return {
        "use_model_api": app_config.use_model_api,
        "api_key": app_config.api_key,
        "base_url": app_config.base_url,
        "model": app_config.retrieve_model,
    }


def build_multitask_registry(app_config: AppConfig) -> AgentRegistry:
    retrieve_kwargs = _build_retrieve_agent_kwargs(app_config)
    registry = AgentRegistry()
    registry.register(
        "retrieve_agent",
        RetrieveAgent(**retrieve_kwargs),
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
        ExtractAgent(),
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
        SynthesizeAgent(),
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
        ReasonAgent(),
        AgentSpec(
            name="reason_agent",
            capabilities=["reason", "analysis"],
            task_types=["reasoning", "aggregation", "summarization"],
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
        VerifyAgent(),
        AgentSpec(
            name="verify_agent",
            capabilities=["verify", "reason"],
            task_types=["verification", "reasoning", "comparison", "calculation"],
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
        RetrieveAgent(**retrieve_kwargs),
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
        ExtractAgent(),
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
        ExtractAgent(),
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
        SynthesizeAgent(),
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


def build_runtime(config: RunConfig) -> Dict[str, Any]:
    app_config = load_app_config_from_env() if config.use_env else AppConfig()
    components = build_online_components(app_config)
    planner = ConstraintAwarePlanner(llm_planner=components["planner"])
    registry = build_multitask_registry(app_config)

    verifiers = [
        SchemaVerifier(),
        RuleVerifier(),
        SemanticVerifier(semantic_judge=components["semantic_judge"]),
        RequirementsAnalysisVerifier(),
        PlanSchemaVerifier(),
        PlanCoverageVerifier(),
        NoPlaceholderVerifier(),
        RetrievalVerifier(),
        ExtractionVerifier(),
        CrossNodeVerifier(),
        CrossNodeGraphVerifier(),
        EdgeConsistencyVerifier(),
        SubtreeIntentVerifier(),
        SummarizationVerifier(),
        ComparisonVerifier(),
        CalculationVerifier(),
        FinalResponseVerifier(),
        CustomNodeVerifier(),
    ]
    if config.enable_intent_verifier:
        verifiers.insert(5, IntentVerifier())
    verifier_engine = VerifierEngine(verifiers=verifiers)

    trace_logger = TraceLogger()
    stop_on_failure = (
        config.stop_on_failure
        if config.stop_on_failure is not None
        else app_config.stop_on_failure
    )
    if not config.enable_replan and config.stop_on_failure is None:
        stop_on_failure = True

    runtime = ExecutionRuntime(
        registry=registry,
        verifier_engine=verifier_engine,
        replanner=ConstraintAwareReplanner(planner=planner) if config.enable_replan else None,
        trace_logger=trace_logger,
        stop_on_failure=stop_on_failure,
        parallel=config.parallel,
        max_runtime_steps=config.max_runtime_steps,
        budget=RuntimeBudget(
            max_runtime_steps=config.max_runtime_steps,
            max_total_attempts=config.max_total_attempts,
            max_total_replans=config.max_total_replans,
        ),
    )
    return {
        "app_config": app_config,
        "components": components,
        "planner": planner,
        "registry": registry,
        "runtime": runtime,
        "trace_logger": trace_logger,
    }


def _resolve_query_and_family(config: RunConfig, app_config: AppConfig) -> tuple[str, str]:
    query = (config.query or app_config.default_query or DEFAULT_QUERY).strip() or DEFAULT_QUERY
    family = resolve_task_family(query=query, explicit_family=config.task_family, planner=ConstraintAwarePlanner(llm_planner=None))
    return query, family


def resolve_task_family(
    query: str,
    explicit_family: Optional[str],
    planner: ConstraintAwarePlanner,
) -> str:
    normalized = (explicit_family or "").strip().lower()
    if normalized:
        return normalized
    inferred = planner.infer_task_family(query)
    return inferred or "qa"


def run_task(config: RunConfig) -> RunResult:
    start_ts = time.time()
    bundle = build_runtime(config)
    app_config: AppConfig = bundle["app_config"]
    planner: ConstraintAwarePlanner = bundle["planner"]
    registry: AgentRegistry = bundle["registry"]
    runtime: ExecutionRuntime = bundle["runtime"]
    trace_logger: TraceLogger = bundle["trace_logger"]
    components = bundle["components"]

    user_query, family = _resolve_query_and_family(config, app_config)
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

    assignment_mode = (config.assignment_mode or app_config.assignment_mode).strip().lower()
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

    report = runtime.execute(
        tree=task_tree,
        context=ExecutionContext(
            global_context={"query": user_query},
            trace_id=str(uuid.uuid4()),
        ),
    )

    trace_path: Optional[str] = None
    if config.export_trace:
        os.makedirs("artifacts", exist_ok=True)
        trace_path = os.path.join("artifacts", f"trace_{int(time.time())}.json")
        trace_logger.export_json(trace_path)
        report.trace_path = trace_path

    metrics = MetricsCollector().summarize(report)
    final_output = report.final_output if isinstance(report.final_output, dict) else {}
    dag_order = task_tree.topo_sort()

    return RunResult(
        query=user_query,
        task_family=family,
        success=bool(report.success),
        final_output=final_output,
        report=report,
        metrics=metrics.__dict__,
        trace_path=trace_path,
        mode=components["mode"],
        dag_order=dag_order,
        total_retries=report.total_retries,
        verification_failures=report.verification_failures,
        replan_count=report.replan_count,
        plan_versions=report.plan_versions,
        budget_exhausted=report.budget_exhausted,
        elapsed_sec=time.time() - start_ts,
        task_tree=task_tree,
    )


def run_batch(config: RunConfig, tasks: List[Dict[str, str]]) -> List[RunResult]:
    results: List[RunResult] = []
    for task in tasks:
        query = str(task.get("query", "")).strip()
        if not query:
            continue
        task_family_value = task.get("task_family", config.task_family)
        family = str(task_family_value).strip().lower() if task_family_value is not None else None
        task_cfg = replace(config, query=query, task_family=family or None)
        results.append(run_task(task_cfg))
    return results


def run_task_collect(
    query: Optional[str] = None,
    task_family: Optional[str] = None,
    stop_on_failure: Optional[bool] = None,
    enable_replan: bool = True,
    enable_intent_verifier: bool = True,
    export_trace: bool = True,
    assignment_mode: Optional[str] = None,
) -> Dict[str, Any]:
    config = RunConfig(
        mode="demo",
        query=query,
        task_family=task_family,
        stop_on_failure=stop_on_failure,
        enable_replan=enable_replan,
        enable_intent_verifier=enable_intent_verifier,
        export_trace=export_trace,
        assignment_mode=assignment_mode,
    )
    return run_task(config).to_legacy_dict()
