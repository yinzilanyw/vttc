from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from svmap.config import load_env_file
from svmap.pipeline import RunConfig, RunResult, run_batch


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
    text = raw.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def load_examples(dataset_path: str, limit: int = 100, task_family_override: str = "") -> List[Dict[str, str]]:
    from svmap.planning import ConstraintAwarePlanner
    
    # 创建规划器实例用于任务类型推断
    planner = ConstraintAwarePlanner(llm_planner=None)
    
    examples: List[Dict[str, str]] = []
    with open(dataset_path, "r", encoding="utf-8-sig") as f:
        for idx, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            item = json.loads(line)
            query = str(item.get("query", "")).strip()
            if not query:
                continue
            
            # 确定任务类型
            if task_family_override.strip():
                # 如果指定了覆盖值，使用覆盖值
                family = task_family_override.strip().lower()
            elif item.get("task_family"):
                # 如果数据集中指定了任务类型，使用指定值
                family = str(item.get("task_family")).strip().lower()
            else:
                # 否则自动推断任务类型
                family = planner.infer_task_family(query)
            
            sample_id = str(item.get("id", f"sample_{idx:04d}")).strip() or f"sample_{idx:04d}"
            examples.append({"id": sample_id, "query": query, "task_family": family})
            if len(examples) >= limit:
                break
    return examples


def _safe_mean(items: List[float]) -> float:
    if not items:
        return 0.0
    return float(sum(items) / max(len(items), 1))


def summarize_batch(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(records)
    if total == 0:
        return {
            "total_examples": 0,
            "task_success_rate": 0.0,
            "structure_success_rate": 0.0,
            "semantic_success_rate": 0.0,
            "verification_failure_rate": 0.0,
            "repair_trigger_rate": 0.0,
            "repair_success_rate": 0.0,
            "generic_output_rate": 0.0,
            "topic_drift_rate": 0.0,
            "deliverable_specificity_rate": 0.0,
            "metric_measurability_rate": 0.0,
            "repo_binding_rate": 0.0,
        }

    verification_failed_cases = sum(1 for r in records if int(r.get("verification_failures", 0)) > 0)
    repair_trigger_cases = sum(1 for r in records if int(r.get("replans", 0)) > 0)
    repair_success_cases = sum(1 for r in records if int(r.get("replans", 0)) > 0 and bool(r.get("semantic_success")))

    metric_values = [r.get("metrics", {}) if isinstance(r.get("metrics"), dict) else {} for r in records]
    return {
        "total_examples": total,
        "task_success_rate": sum(1 for r in records if bool(r.get("success"))) / total,
        "structure_success_rate": sum(1 for r in records if bool(r.get("structure_success"))) / total,
        "semantic_success_rate": sum(1 for r in records if bool(r.get("semantic_success"))) / total,
        "verification_failure_rate": verification_failed_cases / total,
        "repair_trigger_rate": repair_trigger_cases / total,
        "repair_success_rate": (
            repair_success_cases / repair_trigger_cases if repair_trigger_cases > 0 else 0.0
        ),
        "generic_output_rate": _safe_mean([float(m.get("generic_output_rate", 0.0)) for m in metric_values]),
        "topic_drift_rate": _safe_mean([float(m.get("topic_drift_rate", 0.0)) for m in metric_values]),
        "deliverable_specificity_rate": _safe_mean(
            [float(m.get("deliverable_specificity_rate", 0.0)) for m in metric_values]
        ),
        "metric_measurability_rate": _safe_mean(
            [float(m.get("metric_measurability_rate", 0.0)) for m in metric_values]
        ),
        "repo_binding_rate": _safe_mean([float(m.get("repo_binding_rate", 0.0)) for m in metric_values]),
    }


def _ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def save_results(output_dir: str, records: List[Dict[str, Any]]) -> str:
    _ensure_dir(output_dir)
    path = os.path.join(output_dir, "results.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def save_summary(output_dir: str, summary: Dict[str, Any]) -> tuple[str, str]:
    _ensure_dir(output_dir)
    json_path = os.path.join(output_dir, "summary.json")
    csv_path = os.path.join(output_dir, "summary.csv")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary.keys()))
        writer.writeheader()
        writer.writerow(summary)
    return json_path, csv_path


def print_batch_summary(summary: Dict[str, Any]) -> None:
    print("=== SVMAP Batch Summary ===")
    for key in [
        "total_examples",
        "task_success_rate",
        "structure_success_rate",
        "semantic_success_rate",
        "verification_failure_rate",
        "repair_trigger_rate",
        "repair_success_rate",
        "generic_output_rate",
        "topic_drift_rate",
        "deliverable_specificity_rate",
        "metric_measurability_rate",
        "repo_binding_rate",
    ]:
        value = summary.get(key, 0.0)
        if isinstance(value, float):
            print(f"{key}: {value:.4f}")
        else:
            print(f"{key}: {value}")


def _copy_trace_if_needed(result: RunResult, traces_dir: str) -> str:
    source = result.trace_path or ""
    if not source or not os.path.exists(source):
        return ""
    _ensure_dir(traces_dir)
    target_name = f"{int(time.time() * 1000)}_{os.path.basename(source)}"
    target = os.path.join(traces_dir, target_name)
    shutil.copy2(source, target)
    return target


def run_batch_eval(args: argparse.Namespace) -> Dict[str, Any]:
    load_env_file(".env")
    dataset = (
        args.dataset
        or os.getenv("BATCH_DATASET")
        or os.getenv("EVAL_DATASET")
        or "experiments/datasets/demo_multitask.jsonl"
    ).strip()
    if not dataset:
        raise RuntimeError("Missing dataset path. Use --dataset or set BATCH_DATASET in .env.")

    limit = args.limit if args.limit is not None else _env_int("BATCH_MAX_SAMPLES", _env_int("EVAL_MAX_SAMPLES", 100))
    output_dir = (
        args.output_dir
        or os.getenv("BATCH_OUTPUT_DIR")
        or os.path.join("outputs", f"run_{int(time.time())}")
    ).strip()
    save_traces = args.save_traces
    if save_traces is None:
        save_traces = _env_flag("BATCH_SAVE_TRACES", True)
    verbose = bool(args.verbose or _env_flag("BATCH_VERBOSE", False))
    family_override = (args.task_family or os.getenv("BATCH_TASK_FAMILY") or "").strip().lower()

    examples = load_examples(dataset_path=dataset, limit=limit, task_family_override=family_override)
    if not examples:
        raise RuntimeError(f"No valid examples loaded from dataset: {dataset}")

    config = RunConfig(
        mode="batch",
        export_trace=bool(save_traces),
        enable_replan=not (
            _env_flag("BATCH_NO_REPLAN", False) or _env_flag("BATCH_NO_STRUCTURAL_REPAIR", False)
        ),
        enable_intent_verifier=not _env_flag("BATCH_NO_INTENT", False),
        enable_quality_verifier=not _env_flag("BATCH_NO_QUALITY_VERIFIER", False),
        enable_plan_coverage_verifier=not _env_flag("BATCH_NO_PLAN_COVERAGE", False),
        stop_on_failure=_env_optional_bool("BATCH_STOP_ON_FAILURE"),
        assignment_mode=(os.getenv("BATCH_ASSIGNMENT_MODE") or os.getenv("ASSIGNMENT_MODE") or "").strip().lower() or None,
        max_runtime_steps=_env_int("BATCH_MAX_RUNTIME_STEPS", 200),
        max_total_attempts=_env_int("BATCH_MAX_TOTAL_ATTEMPTS", 40),
        max_total_replans=_env_int("BATCH_MAX_TOTAL_REPLANS", 10),
    )
    task_inputs = [{"query": e["query"], "task_family": e["task_family"]} for e in examples]
    run_results = run_batch(config=config, tasks=task_inputs)

    traces_dir = os.path.join(output_dir, "traces")
    records: List[Dict[str, Any]] = []
    for idx, result in enumerate(run_results):
        sample_id = examples[idx]["id"] if idx < len(examples) else f"sample_{idx+1:04d}"
        record = result.to_eval_record(record_id=sample_id)
        if save_traces:
            copied = _copy_trace_if_needed(result=result, traces_dir=traces_dir)
            if copied:
                record["trace_path"] = copied
        if verbose:
            print(
                f"[{idx+1}/{len(run_results)}] id={sample_id} "
                f"family={record['task_family']} structure={record['structure_success']} "
                f"semantic={record['semantic_success']} replans={record['replans']} "
                f"failure={record['primary_failure_type'] or 'none'}"
            )
        records.append(record)

    summary = summarize_batch(records)
    results_path = save_results(output_dir=output_dir, records=records)
    summary_json, summary_csv = save_summary(output_dir=output_dir, summary=summary)
    print_batch_summary(summary)
    print("Saved:", results_path)
    print("Saved:", summary_json)
    print("Saved:", summary_csv)
    if save_traces:
        print("Traces dir:", traces_dir)
    return {
        "output_dir": output_dir,
        "results_path": results_path,
        "summary_json": summary_json,
        "summary_csv": summary_csv,
        "summary": summary,
        "records": records,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SVMAP batch validation.")
    parser.add_argument("--dataset", type=str, default=None, help="Input dataset in jsonl format.")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to write batch outputs.")
    parser.add_argument("--limit", type=int, default=None, help="Max number of examples.")
    parser.add_argument("--task-family", type=str, default=None, help="Override task family for all samples.")
    parser.add_argument(
        "--save-traces",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Whether to save trace files for each sample.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print per-sample progress details.")
    return parser


def run_batch_from_env(env_path: str = ".env", args: Optional[argparse.Namespace] = None) -> Dict[str, Any]:
    load_env_file(env_path)
    ns = args or argparse.Namespace(
        dataset=None,
        output_dir=None,
        limit=None,
        task_family=None,
        save_traces=None,
        verbose=False,
    )
    return run_batch_eval(ns)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    run_batch_eval(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

