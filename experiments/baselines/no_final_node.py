from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from svmap.pipeline import RunConfig, run_task


def run_no_final_node_baseline(query: str, task_family: str = "qa") -> Dict[str, Any]:
    task_result = run_task(
        RunConfig(
            mode="eval",
            query=query,
            task_family=task_family,
            export_trace=False,
        )
    )
    report = task_result.report

    fallback_answer = ""
    if task_result.task_tree is None:
        return {
            "mode": "no_final_node",
            "task_family": task_family,
            "query": query,
            "fallback_answer": fallback_answer,
            "success": False,
            "full_system_success": bool(task_result.success),
        }

    for node_id in reversed(task_result.dag_order):
        node = task_result.task_tree.nodes.get(node_id)
        if node is None or node.is_final_response():
            continue
        rec = report.node_records.get(node_id)
        if rec and isinstance(rec.output, dict):
            for key in ("answer", "summary", "comparison", "result", "ceo", "company", "evidence"):
                value = rec.output.get(key)
                if isinstance(value, str) and value.strip():
                    fallback_answer = value.strip()
                    break
                if isinstance(value, (int, float)):
                    fallback_answer = str(value)
                    break
        if fallback_answer:
            break

    return {
        "mode": "no_final_node",
        "task_family": task_family,
        "query": query,
        "fallback_answer": fallback_answer,
        "success": bool(fallback_answer),
        "full_system_success": bool(task_result.success),
    }


if __name__ == "__main__":
    sample = run_no_final_node_baseline(
        query="Who is the CEO of the company founded by Elon Musk?",
        task_family="qa",
    )
    print(sample)
