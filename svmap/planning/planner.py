from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from svmap.models import TaskTree


def _load_openai_client(api_key: str, base_url: str) -> Any:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "openai package is not installed. Install it with: pip install openai"
        ) from exc
    return OpenAI(api_key=api_key, base_url=base_url)


def _extract_chat_completion_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Could not extract choices from chat completion response.")

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, list):
        chunks: List[str] = []
        for part in content:
            if isinstance(part, dict):
                part_text = part.get("text")
            else:
                part_text = getattr(part, "text", None)
            if isinstance(part_text, str):
                chunks.append(part_text)
        if chunks:
            return "\n".join(chunks).strip()

    raise RuntimeError("Could not extract text from chat completion response.")


class BailianTaskPlanner:
    TASK_TREE_SCHEMA = {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "description": {"type": "string"},
                        "inputs": {"type": "object"},
                        "dependencies": {"type": "array", "items": {"type": "string"}},
                        "agent": {
                            "type": "string",
                            "enum": [
                                "search_agent",
                                "company_agent",
                                "ceo_agent",
                                "ceo_fallback_agent",
                            ],
                        },
                        "fallback_agent": {"type": "string", "enum": ["ceo_fallback_agent"]},
                        "constraint": {"type": "array", "items": {"type": "string"}},
                        "max_retry": {"type": "integer", "minimum": 0, "maximum": 3},
                    },
                    "required": ["id", "description", "dependencies", "agent", "constraint"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["nodes"],
        "additionalProperties": False,
    }

    def __init__(self, api_key: str, base_url: str, model: str = "qwen-plus") -> None:
        self.model = model
        self.client = _load_openai_client(api_key=api_key, base_url=base_url)

    def __call__(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a task-DAG planner. Return strictly valid JSON that matches the "
                        "schema. Build a compact DAG with explicit dependencies."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "task_tree_plan",
                    "description": "Structured task DAG plan for multi-agent execution.",
                    "strict": True,
                    "schema": self.TASK_TREE_SCHEMA,
                },
            },
        )
        return _extract_chat_completion_text(response)


class BailianSemanticJudge:
    JUDGE_SCHEMA = {
        "type": "object",
        "properties": {
            "passed": {"type": "boolean"},
            "reasons": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["passed", "reasons"],
        "additionalProperties": False,
    }

    def __init__(self, api_key: str, base_url: str, model: str = "qwen-flash") -> None:
        self.model = model
        self.client = _load_openai_client(api_key=api_key, base_url=base_url)

    def __call__(
        self,
        output: Dict[str, Any],
        constraints: List[str],
        context: Dict[str, Any],
    ) -> bool:
        if not constraints:
            return True

        payload = {
            "constraints": constraints,
            "output": output,
            "context": {
                "node_inputs": context.get("node_inputs", {}),
                "dependency_outputs": context.get("dependency_outputs", {}),
            },
        }
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict but practical semantic verifier. "
                        "If evidence is insufficient to prove failure, return passed=true."
                    ),
                },
                {
                    "role": "user",
                    "content": "Verify constraints and return JSON only.\n"
                    + json.dumps(payload, ensure_ascii=False),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "semantic_verdict",
                    "description": "Semantic verification verdict for node outputs.",
                    "strict": True,
                    "schema": self.JUDGE_SCHEMA,
                },
            },
        )
        verdict = json.loads(_extract_chat_completion_text(response))
        return bool(verdict.get("passed", False))


@dataclass
class PlanningContext:
    user_query: str
    available_agents: List[str]
    available_tools: List[str]
    global_constraints: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class BasePlanner(ABC):
    @abstractmethod
    def plan(self, context: PlanningContext) -> TaskTree:
        raise NotImplementedError


class ConstraintAwarePlanner(BasePlanner):
    def __init__(self, llm_planner: Optional[Callable[[str], Any]] = None) -> None:
        self.llm_planner = llm_planner

    def _build_prompt(self, context: PlanningContext) -> str:
        return f"""
Generate a task DAG in JSON schema only.
User query: {context.user_query}

Available agents: {context.available_agents}
Global constraints: {context.global_constraints}

Use only these agents:
- search_agent
- company_agent
- ceo_agent
- ceo_fallback_agent

Prefer a 3-node structure:
n1 (extract founder) -> n2 (find company) -> n3 (find ceo)
"""

    def _extract_founder(self, query: str) -> str:
        match = re.search(r"founded by\s+([A-Za-z .'-]+)\??", query, re.IGNORECASE)
        if not match:
            return "Elon Musk"
        return match.group(1).strip()

    def _default_plan(self, context: PlanningContext) -> Dict[str, Any]:
        founder = self._extract_founder(context.user_query)
        return {
            "nodes": [
                {
                    "id": "n1",
                    "description": "Extract founder name from the user query.",
                    "inputs": {"query": context.user_query, "founder_hint": founder},
                    "dependencies": [],
                    "agent": "search_agent",
                    "constraint": ["required_keys:founder", "non_empty_values"],
                    "capability_tag": "search",
                    "io": {
                        "output_fields": [
                            {"name": "founder", "field_type": "string", "required": True}
                        ]
                    },
                },
                {
                    "id": "n2",
                    "description": "Find the company founded by the founder.",
                    "dependencies": ["n1"],
                    "agent": "company_agent",
                    "constraint": ["required_keys:company", "non_empty_values"],
                    "capability_tag": "lookup",
                    "io": {
                        "output_fields": [
                            {"name": "company", "field_type": "string", "required": True}
                        ]
                    },
                },
                {
                    "id": "n3",
                    "description": "Find the CEO of the company.",
                    "dependencies": ["n2"],
                    "agent": "ceo_agent",
                    "fallback_agent": "ceo_fallback_agent",
                    "constraint": ["required_keys:ceo", "non_empty_values", "must_be_factual"],
                    "max_retry": 2,
                    "capability_tag": "reason",
                    "io": {
                        "output_fields": [
                            {"name": "ceo", "field_type": "string", "required": True}
                        ]
                    },
                },
            ]
        }

    def plan(self, context: PlanningContext) -> TaskTree:
        if self.llm_planner is None:
            return TaskTree.from_dict(self._default_plan(context))

        raw = self.llm_planner(self._build_prompt(context))
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            raise TypeError("llm_planner must return a JSON string or dict.")
        return TaskTree.from_dict(data)

    def refine_plan(self, tree: TaskTree, feedback: Dict[str, Any]) -> TaskTree:
        tree.metadata["refine_feedback"] = feedback
        tree.version += 1
        return tree
