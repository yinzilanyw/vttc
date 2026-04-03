from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from svmap.pipeline import RunConfig, run_task


def run_no_replan_baseline(query: str) -> dict:
    result = run_task(
        RunConfig(
            mode="eval",
            query=query,
            stop_on_failure=True,
            enable_replan=False,
            export_trace=False,
        )
    )
    return result.to_legacy_dict()


if __name__ == "__main__":
    result = run_no_replan_baseline("Who is the CEO of the company founded by Elon Musk?")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
