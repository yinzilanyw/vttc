from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from svmap.demos.run_demo import run_demo_collect


def run_no_capability_assignment_baseline(query: str, task_family: str = "qa") -> Dict[str, Any]:
    old_mode = os.getenv("ASSIGNMENT_MODE")
    os.environ["ASSIGNMENT_MODE"] = "naive"
    try:
        result = run_demo_collect(query=query, task_family=task_family, export_trace=False)
    finally:
        if old_mode is None:
            os.environ.pop("ASSIGNMENT_MODE", None)
        else:
            os.environ["ASSIGNMENT_MODE"] = old_mode

    return {
        "mode": "no_capability_assignment",
        "task_family": task_family,
        "query": query,
        "success": bool(result["success"]),
        "retries": int(result["total_retries"]),
        "replans": int(result["replan_count"]),
        "final_answer": str(result.get("final_answer", "")),
    }


if __name__ == "__main__":
    sample = run_no_capability_assignment_baseline(
        query="Who is the CEO of the company founded by Elon Musk?",
        task_family="qa",
    )
    print(sample)
