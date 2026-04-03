from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from experiments.baselines.no_replan import run_no_replan_baseline
from experiments.baselines.no_tree import run_no_tree_baseline
from svmap.config import load_env_file
from svmap.pipeline import DEFAULT_QUERY, RunConfig, run_task


def _truncate(text: str, max_len: int = 48) -> str:
    cleaned = " ".join(str(text).strip().split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_result_row(label: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    metrics = raw.get("metrics", {})

    success = bool(raw.get("success", metrics.get("task_success", raw.get("task_success", False))))
    node_success_rate = _as_float(
        raw.get("node_success_rate", metrics.get("node_success_rate", 0.0))
    )
    verification_failure_count = _as_int(
        raw.get(
            "verification_failure_count",
            metrics.get("verification_failure_count", raw.get("verification_failures", 0)),
        )
    )
    retry_count = _as_int(raw.get("retry_count", metrics.get("retry_count", raw.get("total_retries", 0))))
    replan_count = _as_int(raw.get("replan_count", metrics.get("replan_count", 0)))
    avg_attempts_per_node = _as_float(
        raw.get("avg_attempts_per_node", metrics.get("avg_attempts_per_node", 1.0))
    )

    answer = str(raw.get("final_answer") or raw.get("answer") or "").strip()
    backend = str(raw.get("mode") or raw.get("backend") or "").strip()
    return {
        "mode": label,
        "backend": backend,
        "success": success,
        "node_success_rate": node_success_rate,
        "verification_failures": verification_failure_count,
        "retries": retry_count,
        "replans": replan_count,
        "avg_attempts": avg_attempts_per_node,
        "plan_versions": _as_int(raw.get("plan_versions", 1)),
        "budget_exhausted": bool(raw.get("budget_exhausted", False)),
        "elapsed_sec": _as_float(raw.get("elapsed_sec", 0.0)),
        "answer": answer,
        "error": "",
    }


def _error_result_row(label: str, error: Exception) -> Dict[str, Any]:
    return {
        "mode": label,
        "backend": "",
        "success": False,
        "node_success_rate": 0.0,
        "verification_failures": 0,
        "retries": 0,
        "replans": 0,
        "avg_attempts": 0.0,
        "plan_versions": 0,
        "budget_exhausted": False,
        "elapsed_sec": 0.0,
        "answer": "",
        "error": str(error),
    }


def _run_with_guard(label: str, runner: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        raw = runner()
        return _normalize_result_row(label=label, raw=raw)
    except Exception as exc:
        return _error_result_row(label=label, error=exc)


def _print_table(rows: List[Dict[str, Any]]) -> None:
    headers = [
        "mode",
        "success",
        "node_success_rate",
        "verification_failures",
        "retries",
        "replans",
        "avg_attempts",
        "plan_versions",
        "elapsed_sec",
        "answer",
        "error",
    ]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        values = [
            row["mode"],
            "1" if row["success"] else "0",
            f"{row['node_success_rate']:.2f}",
            str(row["verification_failures"]),
            str(row["retries"]),
            str(row["replans"]),
            f"{row['avg_attempts']:.2f}",
            str(row["plan_versions"]),
            f"{row['elapsed_sec']:.2f}",
            _truncate(row["answer"]).replace("|", "/"),
            _truncate(row["error"]).replace("|", "/"),
        ]
        print("| " + " | ".join(values) + " |")


def _save_artifacts(rows: List[Dict[str, Any]], query: str) -> Dict[str, str]:
    os.makedirs("artifacts", exist_ok=True)
    ts = int(time.time())
    json_path = os.path.join("artifacts", f"ablation_{ts}.json")
    csv_path = os.path.join("artifacts", f"ablation_{ts}.csv")

    payload = {"query": query, "rows": rows}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    fieldnames = list(rows[0].keys()) if rows else []
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return {"json": json_path, "csv": csv_path}


def run_ablation(query: str | None = None, save_artifacts: bool = True) -> List[Dict[str, Any]]:
    load_env_file(".env")
    user_query = (query or os.getenv("DEMO_QUERY") or DEFAULT_QUERY).strip() or DEFAULT_QUERY

    print("=== SVMAP Ablation ===")
    print("Query:", user_query)
    print("Modes: full / no_replan / no_tree")
    print()

    rows = [
        _run_with_guard(
            "full",
            lambda: run_task(
                RunConfig(
                    mode="eval",
                    query=user_query,
                    stop_on_failure=False,
                    export_trace=False,
                )
            ).to_legacy_dict(),
        ),
        _run_with_guard("no_replan", lambda: run_no_replan_baseline(query=user_query)),
        _run_with_guard("no_tree", lambda: run_no_tree_baseline(query=user_query)),
    ]

    _print_table(rows)

    if save_artifacts:
        paths = _save_artifacts(rows=rows, query=user_query)
        print()
        print("Saved:", paths["csv"])
        print("Saved:", paths["json"])
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SVMAP ablation and print comparison table.")
    parser.add_argument("--query", type=str, default=None, help="Evaluation query.")
    parser.add_argument("--no-save", action="store_true", help="Do not write csv/json artifacts.")
    args = parser.parse_args()
    run_ablation(query=args.query, save_artifacts=not args.no_save)
