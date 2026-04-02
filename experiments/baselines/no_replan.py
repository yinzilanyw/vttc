from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from svmap.demos.run_demo import run_demo_collect


def run_no_replan_baseline(query: str) -> dict:
    return run_demo_collect(query=query, stop_on_failure=True)


if __name__ == "__main__":
    result = run_no_replan_baseline("Who is the CEO of the company founded by Elon Musk?")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
