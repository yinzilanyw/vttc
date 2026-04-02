from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from svmap.demos.run_demo import build_default_knowledge_base, load_env_file


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _extract_text_from_completion(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        return ""

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
            else:
                text = getattr(part, "text", None)
            if isinstance(text, str):
                chunks.append(text.strip())
        return " ".join([x for x in chunks if x]).strip()
    return ""


def _answer_with_bailian(query: str) -> Optional[str]:
    if not _env_flag("USE_MODEL_API", default=True):
        return None

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        return None

    base_url = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    model = os.getenv("NO_TREE_MODEL") or os.getenv("PLANNER_MODEL") or "qwen-plus"
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Answer the user question directly in one short sentence. "
                    "If unknown, answer with 'Unknown'."
                ),
            },
            {"role": "user", "content": query},
        ],
    )
    answer = _extract_text_from_completion(response)
    return answer or None


def _answer_with_offline_kb(query: str) -> str:
    kb = build_default_knowledge_base()
    founder_match = re.search(r"founded by\s+([A-Za-z .'-]+)\??", query, re.IGNORECASE)
    if founder_match:
        founder = " ".join(founder_match.group(1).strip().lower().split())
        facts = kb.get(founder, {})
        ceo = facts.get("ceo")
        return ceo or "Unknown"

    company_match = re.search(r"ceo of\s+([A-Za-z0-9 .'-]+)\??", query, re.IGNORECASE)
    if company_match:
        company = " ".join(company_match.group(1).strip().lower().split())
        for facts in kb.values():
            if facts.get("company", "").lower() == company:
                return facts.get("ceo", "Unknown")
    return "Unknown"


def run_no_tree_baseline(query: str) -> dict:
    load_env_file(".env")
    start_ts = time.time()

    answer = _answer_with_bailian(query)
    backend = "bailian_direct"
    if answer is None:
        answer = _answer_with_offline_kb(query)
        backend = "offline_kb"

    success = bool(answer and answer.strip() and answer.strip().lower() != "unknown")
    elapsed_sec = time.time() - start_ts
    return {
        "query": query,
        "answer": answer,
        "mode": "no_tree",
        "backend": backend,
        "task_success": success,
        "task_success_rate": 1.0 if success else 0.0,
        "node_success_rate": 1.0 if success else 0.0,
        "verification_failure_count": 0,
        "retry_count": 0,
        "replan_count": 0,
        "avg_attempts_per_node": 1.0,
        "plan_versions": 1,
        "budget_exhausted": False,
        "elapsed_sec": elapsed_sec,
    }


if __name__ == "__main__":
    sample = "Who is the CEO of the company founded by Elon Musk?"
    print(run_no_tree_baseline(sample))
