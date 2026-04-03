from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from experiments.baselines.no_tree import run_no_tree_baseline
from svmap.pipeline import RunConfig, RunResult, run_batch


def load_dataset(path: str, max_samples: int = 100) -> List[Dict[str, str]]:
    samples: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            item = json.loads(line)
            query = str(item.get("query", "")).strip()
            family = str(item.get("task_family", "qa")).strip().lower()
            if not query:
                continue
            samples.append({"task_family": family, "query": query})
            if len(samples) >= max_samples:
                break
    return samples


def _extract_no_final_answer(result: RunResult) -> str:
    if result.task_tree is None:
        return ""
    report = result.report
    for node_id in reversed(result.dag_order):
        node = result.task_tree.nodes.get(node_id)
        if node is None or node.is_final_response():
            continue
        rec = report.node_records.get(node_id)
        if rec is None or not isinstance(rec.output, dict):
            continue
        output = rec.output
        for key in ("answer", "summary", "comparison", "result", "ceo", "company", "evidence", "extracted"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)):
                return str(value)
    return ""


def summarize_by_task_family(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    family_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        family_map[str(row.get("task_family", "unknown"))].append(row)

    summary: List[Dict[str, Any]] = []
    for family in ["qa", "summary", "compare", "calculate", "extract", "plan"]:
        family_rows = family_map.get(family, [])
        if not family_rows:
            continue
        count = len(family_rows)
        success_rate = sum(int(x["success"]) for x in family_rows) / max(count, 1)
        avg_retries = sum(int(x["retries"]) for x in family_rows) / max(count, 1)
        avg_replans = sum(int(x["replans"]) for x in family_rows) / max(count, 1)
        summary.append(
            {
                "task_family": family,
                "count": count,
                "success_rate": success_rate,
                "avg_retries": avg_retries,
                "avg_replans": avg_replans,
                "avg_semantic_alignment": sum(float(x.get("semantic_alignment_rate", 0.0)) for x in family_rows)
                / max(count, 1),
                "avg_topic_drift_rate": sum(float(x.get("topic_drift_rate", 0.0)) for x in family_rows)
                / max(count, 1),
            }
        )
    return summary


def summarize_plan_family(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    plan_rows = [row for row in rows if str(row.get("task_family", "")) == "plan"]
    if not plan_rows:
        return {
            "plan_semantic_alignment_rate": 0.0,
            "plan_topic_coverage_rate": 0.0,
            "plan_topic_drift_rate": 0.0,
            "plan_replan_trigger_rate": 0.0,
        }
    count = len(plan_rows)
    semantic_alignment_rate = sum(float(r.get("semantic_alignment_rate", 0.0)) for r in plan_rows) / max(count, 1)
    topic_drift_rate = sum(float(r.get("topic_drift_rate", 0.0)) for r in plan_rows) / max(count, 1)
    topic_coverage_rate = sum(float(r.get("coverage_verification_pass_rate", 0.0)) for r in plan_rows) / max(count, 1)
    replan_trigger_rate = sum(1 for r in plan_rows if int(r.get("replans", 0)) > 0) / max(count, 1)
    return {
        "plan_semantic_alignment_rate": semantic_alignment_rate,
        "plan_topic_coverage_rate": topic_coverage_rate,
        "plan_topic_drift_rate": topic_drift_rate,
        "plan_replan_trigger_rate": replan_trigger_rate,
    }


def _build_mode_label(
    no_tree: bool,
    no_intent: bool,
    no_replan: bool,
    no_structural_repair: bool,
    no_final_node: bool,
) -> str:
    if no_tree:
        return "no_tree"
    parts = ["full"]
    if no_intent:
        parts.append("no_intent")
    if no_replan or no_structural_repair:
        parts.append("no_replan")
    if no_final_node:
        parts.append("no_final_node")
    return "+".join(parts)


def run_multitask_eval(
    dataset_path: str,
    max_samples: int = 100,
    save: bool = True,
    no_tree: bool = False,
    no_intent: bool = False,
    no_replan: bool = False,
    no_structural_repair: bool = False,
    no_final_node: bool = False,
) -> Dict[str, Any]:
    samples = load_dataset(dataset_path, max_samples=max_samples)
    rows: List[Dict[str, Any]] = []
    mode_label = _build_mode_label(
        no_tree=no_tree,
        no_intent=no_intent,
        no_replan=no_replan,
        no_structural_repair=no_structural_repair,
        no_final_node=no_final_node,
    )

    if no_tree:
        for idx, sample in enumerate(samples, start=1):
            family = sample["task_family"]
            query = sample["query"]
            print(f"[{idx}/{len(samples)}] {family}: {query[:80]}")
            result = run_no_tree_baseline(query=query)
            rows.append(
                {
                    "mode": mode_label,
                    "task_family": family,
                    "query": query,
                    "success": 1 if bool(result.get("task_success")) else 0,
                    "retries": int(result.get("retry_count", 0)),
                    "replans": int(result.get("replan_count", 0)),
                    "verification_failures": int(result.get("verification_failure_count", 0)),
                    "final_answer": str(result.get("answer", "")),
                    "semantic_alignment_rate": float(result.get("semantic_alignment_rate", 0.0)),
                    "topic_drift_rate": float(result.get("topic_drift_rate", 0.0)),
                    "coverage_verification_pass_rate": float(result.get("coverage_verification_pass_rate", 0.0)),
                }
            )
    else:
        for idx, sample in enumerate(samples, start=1):
            print(f"[{idx}/{len(samples)}] {sample['task_family']}: {sample['query'][:80]}")

        disable_structural_repair = no_replan or no_structural_repair
        config = RunConfig(
            mode="eval",
            export_trace=False,
            enable_intent_verifier=not no_intent,
            enable_replan=not disable_structural_repair,
            stop_on_failure=True if disable_structural_repair else None,
        )
        results = run_batch(config=config, tasks=samples)

        for sample, result in zip(samples, results):
            final_answer = result.final_answer()
            success = result.success
            if no_final_node:
                final_answer = _extract_no_final_answer(result)
                success = bool(final_answer)

            rows.append(
                {
                    "mode": mode_label,
                    "task_family": sample["task_family"],
                    "query": sample["query"],
                    "success": 1 if success else 0,
                    "retries": int(result.total_retries),
                    "replans": int(result.replan_count),
                    "verification_failures": int(result.verification_failures),
                    "final_answer": final_answer,
                    "semantic_alignment_rate": float(result.metrics.get("semantic_alignment_rate", 0.0)),
                    "topic_drift_rate": float(result.metrics.get("topic_drift_rate", 0.0)),
                    "coverage_verification_pass_rate": float(
                        result.metrics.get("coverage_verification_pass_rate", 0.0)
                    ),
                }
            )

    summary_rows = summarize_by_task_family(rows)
    plan_summary = summarize_plan_family(rows)

    print("\n=== Multitask Summary ===")
    print("Mode:", mode_label)
    print("| task_family | count | success_rate | avg_retries | avg_replans |")
    print("|---|---:|---:|---:|---:|")
    for row in summary_rows:
        print(
            f"| {row['task_family']} | {row['count']} | {row['success_rate']:.2f} | "
            f"{row['avg_retries']:.2f} | {row['avg_replans']:.2f} |"
        )
    print("\nPlan-specific metrics:")
    for key, value in plan_summary.items():
        print(f"- {key}: {value:.2f}")

    output = {
        "mode": mode_label,
        "config": {
            "no_tree": no_tree,
            "no_intent": no_intent,
            "no_replan": no_replan,
            "no_structural_repair": no_structural_repair,
            "no_final_node": no_final_node,
        },
        "samples": rows,
        "summary": summary_rows,
        "plan_summary": plan_summary,
    }
    if save:
        os.makedirs("artifacts", exist_ok=True)
        ts = int(time.time())
        json_path = os.path.join("artifacts", f"multitask_eval_{mode_label}_{ts}.json")
        csv_path = os.path.join("artifacts", f"multitask_eval_{mode_label}_{ts}.csv")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            fieldnames = [
                "mode",
                "task_family",
                "query",
                "success",
                "retries",
                "replans",
                "verification_failures",
                "final_answer",
                "semantic_alignment_rate",
                "topic_drift_rate",
                "coverage_verification_pass_rate",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print("Saved:", csv_path)
        print("Saved:", json_path)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run multitask evaluation for SVMAP")
    parser.add_argument(
        "--dataset",
        type=str,
        default="experiments/datasets/demo_multitask.jsonl",
        help="Path to jsonl dataset",
    )
    parser.add_argument("--max-samples", type=int, default=100)
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--no_tree", action="store_true")
    parser.add_argument("--no_intent", action="store_true")
    parser.add_argument("--no_replan", action="store_true")
    parser.add_argument("--no_structural_repair", action="store_true")
    parser.add_argument("--no_final_node", action="store_true")
    args = parser.parse_args()
    run_multitask_eval(
        dataset_path=args.dataset,
        max_samples=args.max_samples,
        save=not args.no_save,
        no_tree=args.no_tree,
        no_intent=args.no_intent,
        no_replan=args.no_replan,
        no_structural_repair=args.no_structural_repair,
        no_final_node=args.no_final_node,
    )
