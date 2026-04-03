from __future__ import annotations

import argparse
import os

from svmap.config import load_env_file


def run_demo_mode(query: str | None = None, task_family: str | None = None) -> int:
    from svmap.demos.run_demo import run_demo

    run_demo(query=query, task_family=task_family)
    return 0


def run_case_study_mode(case_name: str | None = None, query: str | None = None) -> int:
    from svmap.demos.case_study import run_case_study

    run_case_study(case_name=case_name, query=query)
    return 0


def run_eval_mode(dataset_path: str | None = None) -> int:
    from experiments.run_multitask_eval import run_multitask_eval

    path = (dataset_path or os.getenv("EVAL_DATASET") or "experiments/datasets/demo_multitask.jsonl").strip()
    max_samples = int(os.getenv("EVAL_MAX_SAMPLES", "100"))
    run_multitask_eval(dataset_path=path, max_samples=max_samples, save=True)
    return 0


def main() -> int:
    load_env_file(".env")

    parser = argparse.ArgumentParser(description="SVMAP unified app entrypoint")
    parser.add_argument(
        "--mode",
        type=str,
        default=(os.getenv("APP_MODE") or "demo").strip().lower(),
        choices=["demo", "case_study", "eval"],
    )
    parser.add_argument("--query", type=str, default=None)
    parser.add_argument("--task-family", type=str, default=None)
    parser.add_argument("--case-name", type=str, default=os.getenv("CASE_NAME"))
    parser.add_argument("--dataset", type=str, default=os.getenv("EVAL_DATASET"))
    args = parser.parse_args()

    if args.mode == "demo":
        return run_demo_mode(query=args.query, task_family=args.task_family)
    if args.mode == "case_study":
        return run_case_study_mode(case_name=args.case_name, query=args.query)
    return run_eval_mode(dataset_path=args.dataset)
