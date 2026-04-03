from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class AppConfig:
    use_model_api: bool = True
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    planner_model: str = "qwen-plus"
    judge_model: str = "qwen-flash"
    retrieve_model: str = "qwen-flash"
    default_task_family: str = "qa"
    default_query: str = ""
    stop_on_failure: bool = False
    assignment_mode: str = "capability"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and (
                (value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")
            ):
                value = value[1:-1]
            os.environ.setdefault(key, value)


def load_app_config_from_env(env_path: str = ".env") -> AppConfig:
    load_env_file(env_path)

    planner_model = os.getenv("PLANNER_MODEL") or os.getenv("BAILIAN_PLANNER_MODEL") or "qwen-plus"
    judge_model = os.getenv("JUDGE_MODEL") or os.getenv("BAILIAN_JUDGE_MODEL") or "qwen-flash"
    retrieve_model = (
        os.getenv("RETRIEVE_MODEL")
        or os.getenv("BAILIAN_RETRIEVE_MODEL")
        or judge_model
    )
    default_task_family = (os.getenv("DEMO_TASK_FAMILY") or "qa").strip().lower() or "qa"

    return AppConfig(
        use_model_api=_env_flag("USE_MODEL_API", default=True),
        api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
        base_url=os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).strip(),
        planner_model=planner_model.strip() or "qwen-plus",
        judge_model=judge_model.strip() or "qwen-flash",
        retrieve_model=retrieve_model.strip() or "qwen-flash",
        default_task_family=default_task_family,
        default_query=(os.getenv("DEMO_QUERY") or "").strip(),
        stop_on_failure=_env_flag("STOP_ON_FAILURE", default=False),
        assignment_mode=(os.getenv("ASSIGNMENT_MODE") or "capability").strip().lower() or "capability",
    )

