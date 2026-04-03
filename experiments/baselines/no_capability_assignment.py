from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from svmap.pipeline import RunConfig, run_task


def run_no_capability_assignment_baseline(query: str, task_family: str = "qa") -> Dict[str, Any]:
    result = run_task(
        RunConfig(
            mode="eval",
            query=query,
            task_family=task_family,
            assignment_mode="naive",
            export_trace=False,
        )
    ).to_legacy_dict()

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
