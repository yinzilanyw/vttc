from __future__ import annotations

import glob
import os
import shutil
from typing import Optional

from .run_demo import run_demo


def run_case_study(query: Optional[str] = None) -> None:
    if query:
        os.environ["DEMO_QUERY"] = query
    run_demo()


def export_case_study_artifacts(output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    for path in glob.glob(os.path.join("artifacts", "trace_*.json")):
        target = os.path.join(output_dir, os.path.basename(path))
        shutil.copy2(path, target)
