from __future__ import annotations

import json
from pathlib import Path

from .case_study import run_case_study


def run_hotpotqa_benchmark(dataset_path: str, max_samples: int = 100) -> None:
    path = Path(dataset_path)
    if not path.exists():
        raise FileNotFoundError(f"dataset_path not found: {dataset_path}")

    samples = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(samples) >= max_samples:
                break

    for idx, sample in enumerate(samples, start=1):
        query = sample.get("question") or sample.get("query") or sample.get("input")
        if not isinstance(query, str) or not query.strip():
            continue
        print(f"[{idx}/{len(samples)}] Running query: {query[:120]}")
        run_case_study(query=query)
