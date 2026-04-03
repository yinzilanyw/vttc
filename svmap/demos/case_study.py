from __future__ import annotations

import glob
import os
import shutil
from typing import Optional

from svmap.planning import ConstraintAwarePlanner
from svmap.pipeline import RunConfig, run_task


CASE_STUDIES = {
    "qa_basic": "Who is the CEO of the company founded by Elon Musk?",
    "summary_company": "Summarize the key facts about the company founded by Elon Musk.",
    "compare_orgs": "Compare SpaceX and OpenAI in one concise answer.",
}

CASE_FAMILY = {
    "qa_basic": "qa",
    "summary_company": "summary",
    "compare_orgs": "compare",
}


def get_case_query(name: str) -> str:
    if name in CASE_STUDIES:
        return CASE_STUDIES[name]
    available = ", ".join(sorted(CASE_STUDIES.keys()))
    raise KeyError(f"Unknown case name '{name}'. Available: {available}")


def run_case_study(case_name: Optional[str] = None, query: Optional[str] = None) -> None:
    selected_case = (case_name or os.getenv("CASE_NAME") or "qa_basic").strip()
    selected_query = (query or get_case_query(selected_case)).strip()
    if query:
        task_family = ConstraintAwarePlanner(llm_planner=None).infer_task_family(selected_query)
    else:
        task_family = CASE_FAMILY.get(selected_case, "qa")

    result = run_task(
        RunConfig(
            mode="case_study",
            task_family=task_family,
            query=selected_query,
            export_trace=True,
        )
    )

    print("=== Case Study ===")
    print("Case:", selected_case)
    print("Task family:", result.task_family)
    print("Query:", result.query)
    print("Success:", result.success)
    print("Final answer:", result.final_answer())
    print("Trace path:", result.trace_path)


def export_case_study_artifacts(output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    for path in glob.glob(os.path.join("artifacts", "trace_*.json")):
        target = os.path.join(output_dir, os.path.basename(path))
        shutil.copy2(path, target)
