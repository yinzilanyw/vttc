from __future__ import annotations

import os

from svmap.config import load_env_file


def run_single_mode() -> int:
    from svmap.run_single import run_single_from_env

    run_single_from_env(".env")
    return 0


def run_batch_mode() -> int:
    from svmap.run_batch import run_batch_from_env

    run_batch_from_env(".env")
    return 0


def main() -> int:
    load_env_file(".env")
    mode = (os.getenv("APP_MODE") or "single").strip().lower()
    if mode in {"single", "run_single"}:
        return run_single_mode()
    if mode in {"batch", "run_batch", "eval"}:
        return run_batch_mode()
    raise SystemExit(
        "APP_MODE must be one of: single, batch. "
        f"Current value: {mode!r}"
    )
