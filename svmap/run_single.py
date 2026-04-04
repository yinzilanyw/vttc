from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from svmap.config import load_app_config_from_env
from svmap.pipeline import DEFAULT_QUERY, RunConfig, RunResult, run_task


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_optional_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _print_single_result(result: RunResult) -> None:
    report = result.report
    metrics = result.metrics

    print("=== Structured Verifiable Multi-Agent Planning (Single Run) ===")
    print("Mode:", result.mode)
    print("Task family:", result.task_family)
    print("Query:", result.query)
    print("DAG order:", " -> ".join(result.dag_order))
    print("Success:", result.success)
    print("Final answer:", result.final_answer())
    print("Total retries:", result.total_retries)
    print("Verification failures detected:", result.verification_failures)
    print("Replan count:", result.replan_count)
    print("Plan versions:", result.plan_versions)
    print("Trace path:", result.trace_path)
    print()

    for node_id in result.dag_order:
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
        if rec.failure_type:
            print(" failure_type:", rec.failure_type)
        if rec.repair_hint:
            print(" repair_hint:", rec.repair_hint)
        if rec.fatal:
            print(" fatal:", rec.fatal)
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


def _result_to_json(result: RunResult) -> Dict[str, Any]:
    return {
        "mode": result.mode,
        "query": result.query,
        "task_family": result.task_family,
        "success": result.success,
        "dag_order": result.dag_order,
        "total_retries": result.total_retries,
        "verification_failures": result.verification_failures,
        "replan_count": result.replan_count,
        "plan_versions": result.plan_versions,
        "budget_exhausted": result.budget_exhausted,
        "elapsed_sec": result.elapsed_sec,
        "final_output": result.final_output,
        "final_answer": result.final_answer(),
        "metrics": result.metrics,
        "trace_path": result.trace_path,
    }


def run_single_from_env(env_path: str = ".env") -> RunResult:
    app_config = load_app_config_from_env(env_path=env_path)
    query = (
        os.getenv("SINGLE_QUERY")
        or os.getenv("DEFAULT_QUERY")
        or app_config.default_query
        or DEFAULT_QUERY
    ).strip() or DEFAULT_QUERY
    task_family_raw = (
        os.getenv("SINGLE_TASK_FAMILY")
        or os.getenv("DEFAULT_TASK_FAMILY")
        or app_config.default_task_family
        or ""
    ).strip().lower()
    task_family = task_family_raw or None

    assignment_mode = (
        os.getenv("SINGLE_ASSIGNMENT_MODE")
        or os.getenv("ASSIGNMENT_MODE")
        or app_config.assignment_mode
        or ""
    ).strip().lower() or None

    config = RunConfig(
        mode="single",
        query=query,
        task_family=task_family,
        export_trace=_env_flag("SINGLE_EXPORT_TRACE", True),
        enable_replan=_env_flag("SINGLE_ENABLE_REPLAN", True),
        enable_intent_verifier=_env_flag("SINGLE_ENABLE_INTENT_VERIFIER", True),
        enable_quality_verifier=_env_flag("SINGLE_ENABLE_QUALITY_VERIFIER", True),
        enable_plan_coverage_verifier=_env_flag("SINGLE_ENABLE_PLAN_COVERAGE_VERIFIER", True),
        stop_on_failure=_env_optional_bool("SINGLE_STOP_ON_FAILURE"),
        assignment_mode=assignment_mode,
        max_runtime_steps=_env_int("SINGLE_MAX_RUNTIME_STEPS", 200),
        max_total_attempts=_env_int("SINGLE_MAX_TOTAL_ATTEMPTS", 40),
        max_total_replans=_env_int("SINGLE_MAX_TOTAL_REPLANS", 10),
    )
    result = run_task(config)
    _print_single_result(result)

    output_json = (os.getenv("SINGLE_OUTPUT_JSON") or "").strip()
    if output_json:
        output_dir = os.path.dirname(output_json)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(_result_to_json(result), f, ensure_ascii=False, indent=2)
        print("Saved:", output_json)
    return result


def main() -> int:
    run_single_from_env(".env")
    return 0

