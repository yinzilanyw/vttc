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
    GenericOutputVerifier,
    IntentVerifier,
    LowInformationOutputVerifier,
    NoPlaceholderVerifier,
    PlanCoverageVerifier,
    PlanSchemaVerifier,
    RepoBindingVerifier,
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
SEMANTIC_FAILURE_TYPES = {
    "requirements_analysis_failed",
    "topic_extraction_noisy",
    "schema_design_failed",
    "schema_semantics_weak",
    "plan_topic_drift",
    "final_topic_drift",
    "plan_coverage_incomplete",
    "generic_deliverable",
    "non_actionable_metric",
    "repo_binding_weak",
    "generic_plan_output",
    "low_information_output",
    "final_answer_missing_structure",
    "final_placeholder_output",
    "plan_semantic_not_valid",
    "semantic_not_valid",
}


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
    enable_quality_verifier: bool = True
    enable_plan_coverage_verifier: bool = True
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
    structure_success: bool = False
    semantic_success: bool = False
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
    primary_failure_type: str = ""
    repair_action: str = ""
    repair_success: bool = False
    semantic_gaps: List[str] = field(default_factory=list)
    plan_shape: str = ""
    item_count: int = 0
    item_label: str = ""

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
            "structure_success": self.structure_success,
            "semantic_success": self.semantic_success,
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
            "primary_failure_type": self.primary_failure_type,
            "repair_action": self.repair_action,
            "repair_success": self.repair_success,
            "semantic_gaps": list(self.semantic_gaps),
            "plan_shape": self.plan_shape,
            "item_count": self.item_count,
            "item_label": self.item_label,
        }

    def to_eval_record(self, record_id: str = "") -> Dict[str, Any]:
        return {
            "id": record_id,
            "mode": self.mode,
            "query": self.query,
            "task_family": self.task_family,
            "success": bool(self.success),
            "structure_success": bool(self.structure_success),
            "semantic_success": bool(self.semantic_success),
            "retries": int(self.total_retries),
            "replans": int(self.replan_count),
            "verification_failures": int(self.verification_failures),
            "primary_failure_type": self.primary_failure_type,
            "repair_action": self.repair_action,
            "repair_success": bool(self.repair_success),
            "semantic_gaps": list(self.semantic_gaps),
            "plan_shape": self.plan_shape,
            "item_count": int(self.item_count),
            "item_label": self.item_label,
            "final_answer": self.final_answer(),
            "trace_path": self.trace_path or "",
            "elapsed_sec": float(self.elapsed_sec),
            "metrics": dict(self.metrics),
        }


def _pick_primary_failure_type(report: Any) -> str:
    summary = report.failure_summary if isinstance(report.failure_summary, dict) else {}
    ranked = [(str(k), int(v)) for k, v in summary.items() if int(v) > 0 and str(k).strip()]
    if ranked:
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return ranked[0][0]
    for record in report.node_records.values():
        if record.failure_type:
            return str(record.failure_type)
    return ""


def _extract_semantic_gaps(report: Any, final_output: Dict[str, Any]) -> List[str]:
    coverage = final_output.get("coverage_verification")
    if isinstance(coverage, dict) and isinstance(coverage.get("semantic_gaps"), list):
        return [str(x) for x in coverage.get("semantic_gaps", [])]
    verify_record = report.node_records.get("verify_coverage")
    if verify_record is not None and isinstance(verify_record.output, dict):
        gaps = verify_record.output.get("semantic_gaps")
        if isinstance(gaps, list):
            return [str(x) for x in gaps]
    return []


def _compute_structure_success(report: Any, final_output: Dict[str, Any]) -> bool:
    final_node_id = report.final_node_id
    if not final_node_id:
        return False
    final_record = report.node_records.get(final_node_id)
    if final_record is None or final_record.status != "success":
        return False
    answer = final_output.get("answer") or final_output.get("final_response")
    if not isinstance(answer, str) or not answer.strip():
        return False
    if str(report.error or "") in {
        "no_final_response_node",
        "multiple_sink_nodes",
        "sink_not_final_response",
        "final_output_not_valid",
    }:
        return False
    return True


def _compute_semantic_success(report: Any, structure_success: bool, semantic_gaps: List[str]) -> bool:
    if not structure_success:
        return False
    summary = report.failure_summary if isinstance(report.failure_summary, dict) else {}
    for failure_type, count in summary.items():
        if int(count) > 0 and str(failure_type) in SEMANTIC_FAILURE_TYPES:
            return False
    if semantic_gaps:
        return False
    for record in report.node_records.values():
        if getattr(record, "quality_failures", None):
            return False
    return True


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
        RetrievalVerifier(),
        ExtractionVerifier(),
        CrossNodeVerifier(),
        CrossNodeGraphVerifier(),
        EdgeConsistencyVerifier(),
        SubtreeIntentVerifier(),
        SummarizationVerifier(),
        ComparisonVerifier(),
        CalculationVerifier(),
        CustomNodeVerifier(),
    ]
    if config.enable_quality_verifier:
        verifiers[3:3] = [
            RequirementsAnalysisVerifier(),
            PlanSchemaVerifier(),
            *( [PlanCoverageVerifier()] if config.enable_plan_coverage_verifier else [] ),
            RepoBindingVerifier(),
            LowInformationOutputVerifier(),
            GenericOutputVerifier(),
            NoPlaceholderVerifier(),
            FinalResponseVerifier(),
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
    # 直接检查查询是否包含"计划"，如果包含，就返回"plan"类型
    if "计划" in query:
        return query, "plan"
    # 否则使用原来的逻辑
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
    structure_success = _compute_structure_success(report=report, final_output=final_output)
    semantic_gaps = _extract_semantic_gaps(report=report, final_output=final_output)
    semantic_success = _compute_semantic_success(
        report=report,
        structure_success=structure_success,
        semantic_gaps=semantic_gaps,
    )
    primary_failure_type = _pick_primary_failure_type(report=report)
    repair_action = report.replan_actions[-1] if report.replan_actions else ""
    repair_success = report.replan_count > 0 and semantic_success

    return RunResult(
        query=user_query,
        task_family=family,
        success=bool(report.success),
        structure_success=structure_success,
        semantic_success=semantic_success,
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
        primary_failure_type=primary_failure_type,
        repair_action=repair_action,
        repair_success=repair_success,
        semantic_gaps=semantic_gaps,
        plan_shape=str(report.plan_shape or ""),
        item_count=int(report.item_count or 0),
        item_label=str(report.item_label or ""),
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
