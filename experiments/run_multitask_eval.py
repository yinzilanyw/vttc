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

from svmap.demos.run_demo import run_demo_collect


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


def run_multitask_eval(dataset_path: str, max_samples: int = 100, save: bool = True) -> Dict[str, Any]:
    samples = load_dataset(dataset_path, max_samples=max_samples)
    rows: List[Dict[str, Any]] = []
    reports_by_family: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for idx, sample in enumerate(samples, start=1):
        family = sample["task_family"]
        query = sample["query"]
        print(f"[{idx}/{len(samples)}] {family}: {query[:80]}")
        result = run_demo_collect(query=query, task_family=family, export_trace=False)
        row = {
            "task_family": family,
            "query": query,
            "success": 1 if result["success"] else 0,
            "retries": int(result["total_retries"]),
            "replans": int(result["replan_count"]),
            "verification_failures": int(result["verification_failures"]),
            "final_answer": str(result.get("final_answer", "")),
        }
        rows.append(row)
        reports_by_family[family].append(row)

    summary_rows: List[Dict[str, Any]] = []
    for family, family_rows in reports_by_family.items():
        count = len(family_rows)
        success_rate = sum(int(x["success"]) for x in family_rows) / max(count, 1)
        avg_retries = sum(int(x["retries"]) for x in family_rows) / max(count, 1)
        avg_replans = sum(int(x["replans"]) for x in family_rows) / max(count, 1)
        summary_rows.append(
            {
                "task_family": family,
                "count": count,
                "success_rate": success_rate,
                "avg_retries": avg_retries,
                "avg_replans": avg_replans,
            }
        )

    print("\n=== Multitask Summary ===")
    print("| task_family | count | success_rate | avg_retries | avg_replans |")
    print("|---|---:|---:|---:|---:|")
    for row in sorted(summary_rows, key=lambda x: x["task_family"]):
        print(
            f"| {row['task_family']} | {row['count']} | {row['success_rate']:.2f} | "
            f"{row['avg_retries']:.2f} | {row['avg_replans']:.2f} |"
        )

    output = {"samples": rows, "summary": summary_rows}
    if save:
        os.makedirs("artifacts", exist_ok=True)
        ts = int(time.time())
        json_path = os.path.join("artifacts", f"multitask_eval_{ts}.json")
        csv_path = os.path.join("artifacts", f"multitask_eval_{ts}.csv")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            fieldnames = ["task_family", "query", "success", "retries", "replans", "verification_failures", "final_answer"]
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
    args = parser.parse_args()
    run_multitask_eval(dataset_path=args.dataset, max_samples=args.max_samples, save=not args.no_save)
