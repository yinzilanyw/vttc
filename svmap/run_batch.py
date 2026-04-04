from __future__ import annotations

import os
from typing import Any, Dict

from experiments.run_multitask_eval import run_multitask_eval
from svmap.config import load_env_file


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def run_batch_from_env(env_path: str = ".env") -> Dict[str, Any]:
    load_env_file(env_path)
    dataset = (
        os.getenv("BATCH_DATASET")
        or os.getenv("EVAL_DATASET")
        or "experiments/datasets/demo_multitask.jsonl"
    ).strip()
    max_samples = _env_int("BATCH_MAX_SAMPLES", _env_int("EVAL_MAX_SAMPLES", 100))
    save = _env_flag("BATCH_SAVE_ARTIFACTS", True)

    return run_multitask_eval(
        dataset_path=dataset,
        max_samples=max_samples,
        save=save,
        no_tree=_env_flag("BATCH_NO_TREE", False),
        no_intent=_env_flag("BATCH_NO_INTENT", False),
        no_replan=_env_flag("BATCH_NO_REPLAN", False),
        no_structural_repair=_env_flag("BATCH_NO_STRUCTURAL_REPAIR", False),
        no_final_node=_env_flag("BATCH_NO_FINAL_NODE", False),
        no_plan_coverage=_env_flag("BATCH_NO_PLAN_COVERAGE", False),
        no_quality_verifier=_env_flag("BATCH_NO_QUALITY_VERIFIER", False),
    )


def main() -> int:
    run_batch_from_env(".env")
    return 0

