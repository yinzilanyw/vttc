from __future__ import annotations

import argparse
import json
import os
from typing import Optional

from svmap.config import load_app_config_from_env, load_env_file
from svmap.pipeline import DEFAULT_QUERY, RunConfig, RunResult, run_task


def _env_flag(name: str, default: bool = False) -> bool:
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
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def print_single_summary(result: RunResult) -> None:
    print("=== SVMAP Single Validation ===")
    print("Mode:", result.mode)
    print("Task family:", result.task_family)
    print("Query:", result.query)
    if result.task_family == "plan":
        print("Plan shape:", result.plan_shape or "")
        print("Item label:", result.item_label or "")
        print("Item count:", int(result.item_count or 0))
    print("Structure success:", result.structure_success)
    print("Semantic success:", result.semantic_success)
    print("Success:", result.success)
    print("Retries:", result.total_retries)
    print("Replans:", result.replan_count)
    print("Verification failures:", result.verification_failures)
    print("Primary failure type:", result.primary_failure_type or "none")
    print("Repair action:", result.repair_action or "none")
    print("Repair success:", result.repair_success)
    print("Semantic gaps:", result.semantic_gaps if result.semantic_gaps else [])
    print("Final answer:", result.final_answer())
    print("Trace path:", result.trace_path or "")


def print_single_verbose(result: RunResult) -> None:
    report = result.report
    print_single_summary(result)
    print("DAG order:", " -> ".join(result.dag_order))
    print("Plan versions:", result.plan_versions)
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


def _build_single_run_config(args: argparse.Namespace) -> tuple[RunConfig, str, bool]:
    app_cfg = load_app_config_from_env(".env")
    query = (
        args.query
        or os.getenv("SINGLE_QUERY")
        or os.getenv("DEFAULT_QUERY")
        or app_cfg.default_query
        or DEFAULT_QUERY
    ).strip() or DEFAULT_QUERY
    task_family_text = (
        args.task_family
        or os.getenv("SINGLE_TASK_FAMILY")
        or os.getenv("DEFAULT_TASK_FAMILY")
        or app_cfg.default_task_family
        or ""
    ).strip().lower()
    task_family = task_family_text or None

    save_trace = args.save_trace
    if save_trace is None:
        save_trace = _env_flag("SINGLE_EXPORT_TRACE", True)
    verbose = bool(args.verbose or _env_flag("SINGLE_VERBOSE", False))

    stop_on_failure = _env_optional_bool("SINGLE_STOP_ON_FAILURE")
    assignment_mode = (
        os.getenv("SINGLE_ASSIGNMENT_MODE")
        or os.getenv("ASSIGNMENT_MODE")
        or app_cfg.assignment_mode
        or ""
    ).strip().lower() or None

    config = RunConfig(
        mode="single",
        query=query,
        task_family=task_family,
        export_trace=bool(save_trace),
        enable_replan=_env_flag("SINGLE_ENABLE_REPLAN", True),
        enable_intent_verifier=_env_flag("SINGLE_ENABLE_INTENT_VERIFIER", True),
        enable_quality_verifier=_env_flag("SINGLE_ENABLE_QUALITY_VERIFIER", True),
        enable_plan_coverage_verifier=_env_flag("SINGLE_ENABLE_PLAN_COVERAGE_VERIFIER", True),
        stop_on_failure=stop_on_failure,
        assignment_mode=assignment_mode,
        max_runtime_steps=_env_int("SINGLE_MAX_RUNTIME_STEPS", 200),
        max_total_attempts=_env_int("SINGLE_MAX_TOTAL_ATTEMPTS", 40),
        max_total_replans=_env_int("SINGLE_MAX_TOTAL_REPLANS", 10),
    )
    output_path = (
        args.output
        or os.getenv("SINGLE_OUTPUT_JSON")
        or ""
    ).strip()
    return config, output_path, verbose


def run_single_from_env(env_path: str = ".env", args: Optional[argparse.Namespace] = None) -> RunResult:
    load_env_file(env_path)
    ns = args or argparse.Namespace(
        query=None,
        task_family=None,
        output=None,
        save_trace=None,
        verbose=False,
    )
    config, output_path, verbose = _build_single_run_config(ns)
    result = run_task(config)
    if verbose:
        print_single_verbose(result)
    else:
        print_single_summary(result)

    if output_path:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result.to_eval_record(), f, ensure_ascii=False, indent=2)
        print("Saved:", output_path)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one SVMAP validation task.")
    parser.add_argument("--query", type=str, default=None, help="Single query to execute.")
    parser.add_argument(
        "--task-family",
        type=str,
        default=None,
        help="Optional task family override, e.g. plan/qa/summary/compare.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional JSON output path for machine-readable result.",
    )
    parser.add_argument(
        "--save-trace",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Whether to export trace file for this run.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-node details in addition to summary.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    run_single_from_env(".env", args=args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
