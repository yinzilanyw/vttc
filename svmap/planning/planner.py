
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from svmap.models import (
    AllDaysPresentConstraint,
    CoverageConstraint,
    FinalStructureConstraint,
    IntentAlignmentConstraint,
    IntentSpec,
    NoInternalErrorConstraint,
    NoTemplatePlaceholderConstraint,
    NonEmptyExtractionConstraint,
    NonTrivialTransformationConstraint,
    PlanTopicCoverageConstraint,
    TaskNode,
    TaskTree,
)


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


def _infer_capability_from_agent(agent_name: str) -> str:
    lowered = agent_name.lower()
    if "search" in lowered or "retrieve" in lowered:
        return "retrieve"
    if "extract" in lowered or "company" in lowered:
        return "extract"
    if "summar" in lowered:
        return "summarize"
    if "compare" in lowered:
        return "compare"
    if "calculate" in lowered or "calc" in lowered:
        return "calculate"
    if "synth" in lowered or "final" in lowered:
        return "synthesize"
    if "ceo" in lowered or "reason" in lowered:
        return "reason"
    return "reason"


def _default_output_mode(task_type: str, capability_tag: str) -> str:
    if task_type == "calculation" or capability_tag == "calculate":
        return "number"
    if task_type == "comparison" or capability_tag == "compare":
        return "table"
    if task_type == "extraction" or capability_tag == "extract":
        return "json"
    if task_type == "final_response":
        return "text"
    return "text"


def _default_node_type(capability_tag: str) -> str:
    mapping = {
        "retrieve": "tool_call",
        "extract": "extraction",
        "summarize": "summarization",
        "compare": "comparison",
        "calculate": "calculation",
        "verify": "verification",
        "synthesize": "final_response",
        "reason": "reasoning",
    }
    return mapping.get(capability_tag, "reasoning")


def _multitask_schema() -> Dict[str, Any]:
    return {
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
                        "capability_tag": {
                            "type": "string",
                            "enum": [
                                "retrieve",
                                "extract",
                                "summarize",
                                "compare",
                                "calculate",
                                "synthesize",
                                "verify",
                                "reason",
                            ],
                        },
                        "candidate_capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "node_type": {
                            "type": "string",
                            "enum": [
                                "tool_call",
                                "reasoning",
                                "extraction",
                                "summarization",
                                "comparison",
                                "calculation",
                                "verification",
                                "aggregation",
                                "final_response",
                            ],
                        },
                        "task_type": {"type": "string"},
                        "output_mode": {
                            "type": "string",
                            "enum": ["text", "json", "table", "boolean", "number"],
                        },
                        "answer_role": {
                            "type": "string",
                            "enum": ["intermediate", "final"],
                        },
                        "constraint": {"type": "array", "items": {"type": "string"}},
                        "max_retry": {"type": "integer", "minimum": 0, "maximum": 3},
                    },
                    "required": ["id", "description", "dependencies", "capability_tag", "node_type"],
                    "additionalProperties": True,
                },
            }
        },
        "required": ["nodes"],
        "additionalProperties": False,
    }


class BailianTaskPlanner:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "qwen-plus",
        schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.model = model
        self.client = _load_openai_client(api_key=api_key, base_url=base_url)
        self.schema = schema or _multitask_schema()

    def __call__(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a task-DAG planner. Build a compact multi-task DAG. "
                        "Always include a final_response node with answer_role=final."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "multitask_task_tree_plan",
                    "description": "Multi-task structured DAG plan for capability-based agents.",
                    "strict": True,
                    "schema": self.schema,
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
            "confidence": {"type": "number"},
            "repair_hint": {"type": "string"},
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
    ) -> Dict[str, Any]:
        if not constraints:
            return {
                "passed": True,
                "reason": "",
                "confidence": 1.0,
                "repair_hint": "",
            }

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
        reasons = verdict.get("reasons", [])
        reason = "; ".join(reasons) if isinstance(reasons, list) else str(reasons)
        return {
            "passed": bool(verdict.get("passed", False)),
            "reason": reason,
            "confidence": float(verdict.get("confidence", 0.7)),
            "repair_hint": str(verdict.get("repair_hint", "")),
        }


@dataclass
class PlanningContext:
    user_query: str
    available_agents: List[str]
    available_tools: List[str]
    global_goal: str = ""
    global_constraints: List[str] = field(default_factory=list)
    failure_context: Optional[Dict[str, Any]] = None
    replan_scope: str = "none"
    budget: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    task_family: str = ""


class BasePlanner(ABC):
    @abstractmethod
    def plan(self, context: PlanningContext) -> TaskTree:
        raise NotImplementedError

    @abstractmethod
    def replan_subtree(
        self,
        tree: TaskTree,
        failed_node_id: str,
        context: PlanningContext,
    ) -> List[TaskNode]:
        raise NotImplementedError


class ConstraintAwarePlanner(BasePlanner):
    def __init__(self, llm_planner: Optional[Callable[[str], Any]] = None) -> None:
        self.llm_planner = llm_planner

    def build_task_taxonomy_prompt(self) -> str:
        return (
            "Task taxonomy:\n"
            "- capability_tag: retrieve / extract / summarize / compare / calculate / synthesize / verify / reason\n"
            "- node_type: tool_call / reasoning / extraction / summarization / comparison / calculation / aggregation / final_response\n"
            "- output_mode: text / json / table / boolean / number\n"
            "Rules:\n"
            "1) Use capability tags, never fixed agent names.\n"
            "2) Include exactly one final_response node with answer_role=final.\n"
            "3) Final node must depend on all sink nodes.\n"
        )

    def infer_task_family(self, user_query: str) -> str:
        text = user_query.lower().strip()
        if any(
            k in text
            for k in [
                "learning plan",
                "7-day",
                "daily goals",
                "deliverables",
                "metric",
                "roadmap",
                "plan",
            ]
        ):
            return "plan"
        if any(k in text for k in ["structured generation", "schema", "json schema", "format as json"]):
            return "structured_generation"
        if any(k in text for k in ["summarize", "summary", "tl;dr", "概括", "总结"]):
            return "summary"
        if any(k in text for k in ["compare", "difference", "vs", "versus", "对比", "比较"]):
            return "compare"
        if any(
            k in text
            for k in [
                "calculate",
                "compute",
                "total",
                "sum",
                "multiply",
                "plus",
                "precision",
                "recall",
                "rate",
                "ratio",
                "比例",
                "计算",
                "减",
                "加",
                "乘",
                "除",
            ]
        ):
            return "calculate"
        if any(k in text for k in ["extract", "fields", "json", "结构化", "提取"]):
            return "extract"
        return "qa"

    def infer_plan_focus(self, user_query: str) -> str:
        text = user_query.lower().strip()
        planning_signals = ["learning plan", "7-day", "daily goals", "deliverables", "metric"]
        svmap_signals = ["multi-agent", "workflow", "verifiable task tree", "verifiable task trees"]
        if any(x in text for x in planning_signals) and any(x in text for x in svmap_signals):
            return "svmap_learning"
        if self.infer_task_family(user_query) == "plan":
            return "general_plan"
        return ""

    def build_multitask_schema(self) -> Dict[str, Any]:
        return _multitask_schema()

    def normalize_planner_output(self, raw_plan: Dict[str, Any]) -> Dict[str, Any]:
        nodes = raw_plan.get("nodes", [])
        if not isinstance(nodes, list):
            nodes = []

        normalized_nodes: List[Dict[str, Any]] = []
        for idx, raw in enumerate(nodes, start=1):
            if not isinstance(raw, dict):
                continue

            capability_tag = str(raw.get("capability_tag", "")).strip().lower()
            if not capability_tag and raw.get("agent"):
                capability_tag = _infer_capability_from_agent(str(raw.get("agent", "")))
            if not capability_tag:
                capability_tag = "reason"

            node_type = str(raw.get("node_type") or raw.get("task_type") or "").strip().lower()
            if not node_type:
                node_type = _default_node_type(capability_tag)

            output_mode = str(raw.get("output_mode", "")).strip().lower()
            if not output_mode:
                output_mode = _default_output_mode(node_type, capability_tag)

            answer_role = str(raw.get("answer_role", "")).strip().lower()
            if answer_role not in {"final", "intermediate"}:
                answer_role = "final" if node_type == "final_response" else "intermediate"

            candidate_capabilities = raw.get("candidate_capabilities", [])
            if not isinstance(candidate_capabilities, list):
                candidate_capabilities = []
            if capability_tag not in candidate_capabilities:
                candidate_capabilities = [capability_tag] + candidate_capabilities

            constraints = raw.get("constraint") or raw.get("constraints") or []
            if not isinstance(constraints, list):
                constraints = []

            normalized_nodes.append(
                {
                    "id": str(raw.get("id") or f"n{idx}"),
                    "description": str(raw.get("description") or f"{node_type} node"),
                    "inputs": raw.get("inputs", {}) if isinstance(raw.get("inputs", {}), dict) else {},
                    "dependencies": list(raw.get("dependencies", []))
                    if isinstance(raw.get("dependencies", []), list)
                    else [],
                    "capability_tag": capability_tag,
                    "candidate_capabilities": candidate_capabilities,
                    "node_type": node_type,
                    "task_type": node_type,
                    "output_mode": output_mode,
                    "answer_role": answer_role,
                    "constraint": constraints,
                    "max_retry": int(raw.get("max_retry", 2)),
                    "io": raw.get("io", {}) if isinstance(raw.get("io", {}), dict) else {},
                    "metadata": raw.get("metadata", {}) if isinstance(raw.get("metadata", {}), dict) else {},
                }
            )

        plan = {"nodes": normalized_nodes}
        if not any(str(n.get("answer_role")) == "final" for n in normalized_nodes):
            sink_ids = self._sink_ids_from_raw(normalized_nodes)
            plan["nodes"].append(
                {
                    "id": "final_response",
                    "description": "Generate final response for the user.",
                    "dependencies": sink_ids,
                    "capability_tag": "synthesize",
                    "candidate_capabilities": ["synthesize", "reason"],
                    "node_type": "final_response",
                    "task_type": "final_response",
                    "output_mode": "text",
                    "answer_role": "final",
                    "constraint": ["required_keys:answer", "non_empty_values"],
                    "io": {
                        "output_fields": [
                            {"name": "answer", "field_type": "string", "required": True}
                        ]
                    },
                }
            )
        return plan

    def _sink_ids_from_raw(self, nodes: List[Dict[str, Any]]) -> List[str]:
        ids = [str(node.get("id", "")) for node in nodes if node.get("id")]
        deps = set()
        for node in nodes:
            for dep in node.get("dependencies", []):
                deps.add(str(dep))
        return [node_id for node_id in ids if node_id not in deps]

    def _build_prompt(self, context: PlanningContext) -> str:
        family = context.task_family or self.infer_task_family(context.user_query)
        plan_focus = self.infer_plan_focus(context.user_query) if family == "plan" else ""
        return (
            "Generate a task DAG in JSON only.\n"
            f"User query: {context.user_query}\n"
            f"Task family: {family}\n"
            f"Plan focus: {plan_focus}\n"
            f"Available agents: {context.available_agents}\n"
            f"Global goal: {context.global_goal}\n"
            f"Global constraints: {context.global_constraints}\n\n"
            f"{self.build_task_taxonomy_prompt()}\n"
            "Output must follow the schema and include compact dependencies.\n"
        )

    def _default_plan(self, context: PlanningContext) -> Dict[str, Any]:
        family = context.task_family or self.infer_task_family(context.user_query)
        query = context.user_query
        if family == "plan":
            day_nodes: List[Dict[str, Any]] = []
            for day in range(1, 8):
                day_nodes.append(
                    {
                        "id": f"generate_day{day}",
                        "description": f"Generate structured day {day} plan object.",
                        "inputs": {
                            "day": day,
                            "query": query,
                        },
                        "dependencies": ["analyze_requirements", "design_plan_schema"],
                        "capability_tag": "synthesize",
                        "candidate_capabilities": ["synthesize", "reason"],
                        "node_type": "aggregation",
                        "task_type": "aggregation",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": [
                            "required_keys:day,goal,deliverable,metric",
                            "non_empty_values",
                        ],
                        "io": {
                            "output_fields": [
                                {"name": "day", "field_type": "number", "required": True},
                                {"name": "goal", "field_type": "string", "required": True},
                                {"name": "deliverable", "field_type": "string", "required": True},
                                {"name": "metric", "field_type": "string", "required": True},
                            ]
                        },
                    }
                )
            verify_dependencies = ["design_plan_schema"] + [f"generate_day{day}" for day in range(1, 8)]
            return {
                "nodes": [
                    {
                        "id": "analyze_requirements",
                        "description": "Analyze requirements from query into structured constraints.",
                        "inputs": {"query": query},
                        "dependencies": [],
                        "capability_tag": "reason",
                        "candidate_capabilities": ["reason", "synthesize"],
                        "node_type": "reasoning",
                        "task_type": "reasoning",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": [
                            (
                                "required_keys:primary_domain,secondary_focus,task_form,topics,"
                                "must_cover_topics,forbidden_topic_drift,constraints,required_fields,duration_days"
                            ),
                            "non_empty_values",
                        ],
                        "io": {
                            "output_fields": [
                                {"name": "primary_domain", "field_type": "string", "required": True},
                                {"name": "secondary_focus", "field_type": "string", "required": True},
                                {"name": "task_form", "field_type": "string", "required": True},
                                {"name": "topics", "field_type": "list[string]", "required": True},
                                {"name": "must_cover_topics", "field_type": "list[string]", "required": True},
                                {"name": "forbidden_topic_drift", "field_type": "list[string]", "required": True},
                                {"name": "constraints", "field_type": "list[string]", "required": True},
                                {"name": "required_fields", "field_type": "list[string]", "required": True},
                                {"name": "duration_days", "field_type": "number", "required": True},
                            ]
                        },
                    },
                    {
                        "id": "design_plan_schema",
                        "description": "Design canonical day-level schema and progression for the 7-day plan.",
                        "dependencies": ["analyze_requirements"],
                        "capability_tag": "reason",
                        "candidate_capabilities": ["reason", "synthesize"],
                        "node_type": "reasoning",
                        "task_type": "reasoning",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": [
                            "required_keys:day_template,progression,topic_allocation,required_fields",
                            "non_empty_values",
                        ],
                        "io": {
                            "output_fields": [
                                {"name": "day_template", "field_type": "json", "required": True},
                                {"name": "progression", "field_type": "list[string]", "required": True},
                                {"name": "topic_allocation", "field_type": "json", "required": True},
                                {"name": "required_fields", "field_type": "list[string]", "required": True},
                            ]
                        },
                    },
                    *day_nodes,
                    {
                        "id": "verify_coverage",
                        "description": "Verify day coverage, field completeness and semantic alignment.",
                        "dependencies": verify_dependencies,
                        "capability_tag": "verify",
                        "candidate_capabilities": ["verify", "reason"],
                        "node_type": "verification",
                        "task_type": "verification",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": [
                            "required_keys:coverage_ok,missing_days,missing_fields,semantic_gaps,grounded_nodes",
                            "coverage_constraint",
                            "all_days_present",
                            "plan_topic_coverage",
                            "no_template_placeholder",
                        ],
                        "io": {
                            "output_fields": [
                                {"name": "coverage_ok", "field_type": "bool", "required": True},
                                {"name": "missing_days", "field_type": "json", "required": True},
                                {"name": "missing_fields", "field_type": "json", "required": True},
                                {"name": "semantic_gaps", "field_type": "json", "required": True},
                                {"name": "grounded_nodes", "field_type": "json", "required": True},
                            ]
                        },
                    },
                    {
                        "id": "final_response",
                        "description": "Return final 7-day learning plan using verified day objects.",
                        "dependencies": ["verify_coverage"] + [f"generate_day{day}" for day in range(1, 8)],
                        "capability_tag": "synthesize",
                        "candidate_capabilities": ["synthesize", "reason"],
                        "node_type": "final_response",
                        "task_type": "final_response",
                        "output_mode": "text",
                        "answer_role": "final",
                        "constraint": [
                            "required_keys:answer,used_nodes",
                            "non_empty_values",
                            "final_structure:min_items=7,required_sections=goal|deliverable|metric,forbid_query_echo=true",
                        ],
                        "io": {
                            "output_fields": [
                                {"name": "answer", "field_type": "string", "required": True},
                                {"name": "used_nodes", "field_type": "json", "required": True},
                            ]
                        },
                    },
                ]
            }
        if family == "summary":
            return {
                "nodes": [
                    {
                        "id": "n1",
                        "description": "Retrieve evidence for summarization.",
                        "inputs": {"query": query},
                        "dependencies": [],
                        "capability_tag": "retrieve",
                        "candidate_capabilities": ["retrieve", "extract"],
                        "node_type": "tool_call",
                        "task_type": "tool_call",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:evidence", "non_empty_values"],
                    },
                    {
                        "id": "n2",
                        "description": "Summarize retrieved evidence.",
                        "dependencies": ["n1"],
                        "capability_tag": "summarize",
                        "candidate_capabilities": ["summarize", "reason"],
                        "node_type": "summarization",
                        "task_type": "summarization",
                        "output_mode": "text",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:summary", "non_empty_values"],
                    },
                    {
                        "id": "final_response",
                        "description": "Return concise final summary.",
                        "dependencies": ["n2"],
                        "capability_tag": "synthesize",
                        "candidate_capabilities": ["synthesize", "summarize"],
                        "node_type": "final_response",
                        "task_type": "final_response",
                        "output_mode": "text",
                        "answer_role": "final",
                        "constraint": ["required_keys:answer", "non_empty_values"],
                    },
                ]
            }

        if family == "compare":
            return {
                "nodes": [
                    {
                        "id": "n1",
                        "description": "Retrieve evidence for item A from user query.",
                        "inputs": {"query": query, "side": "A"},
                        "dependencies": [],
                        "capability_tag": "retrieve",
                        "candidate_capabilities": ["retrieve", "extract"],
                        "node_type": "tool_call",
                        "task_type": "tool_call",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:evidence", "non_empty_values"],
                    },
                    {
                        "id": "n2",
                        "description": "Retrieve evidence for item B from user query.",
                        "inputs": {"query": query, "side": "B"},
                        "dependencies": [],
                        "capability_tag": "retrieve",
                        "candidate_capabilities": ["retrieve", "extract"],
                        "node_type": "tool_call",
                        "task_type": "tool_call",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:evidence", "non_empty_values"],
                    },
                    {
                        "id": "n3",
                        "description": "Compare item A and item B using retrieved evidence.",
                        "dependencies": ["n1", "n2"],
                        "capability_tag": "compare",
                        "candidate_capabilities": ["compare", "reason"],
                        "node_type": "comparison",
                        "task_type": "comparison",
                        "output_mode": "table",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:comparison", "non_empty_values"],
                    },
                    {
                        "id": "final_response",
                        "description": "Generate final comparison answer.",
                        "dependencies": ["n3"],
                        "capability_tag": "synthesize",
                        "candidate_capabilities": ["synthesize", "compare"],
                        "node_type": "final_response",
                        "task_type": "final_response",
                        "output_mode": "text",
                        "answer_role": "final",
                        "constraint": ["required_keys:answer", "non_empty_values"],
                    },
                ]
            }

        if family == "calculate":
            return {
                "nodes": [
                    {
                        "id": "n1",
                        "description": "Extract numeric expression from query.",
                        "inputs": {"query": query},
                        "dependencies": [],
                        "capability_tag": "extract",
                        "candidate_capabilities": ["extract", "reason"],
                        "node_type": "extraction",
                        "task_type": "extraction",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:expression", "non_empty_values"],
                    },
                    {
                        "id": "n2",
                        "description": "Calculate numeric result from expression.",
                        "dependencies": ["n1"],
                        "capability_tag": "calculate",
                        "candidate_capabilities": ["calculate", "reason"],
                        "node_type": "calculation",
                        "task_type": "calculation",
                        "output_mode": "number",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:result", "non_empty_values"],
                    },
                    {
                        "id": "final_response",
                        "description": "Return final calculation answer.",
                        "dependencies": ["n2"],
                        "capability_tag": "synthesize",
                        "candidate_capabilities": ["synthesize", "calculate"],
                        "node_type": "final_response",
                        "task_type": "final_response",
                        "output_mode": "text",
                        "answer_role": "final",
                        "constraint": ["required_keys:answer", "non_empty_values"],
                    },
                ]
            }

        if family == "extract":
            return {
                "nodes": [
                    {
                        "id": "n1",
                        "description": "Retrieve raw content for field extraction.",
                        "inputs": {"query": query},
                        "dependencies": [],
                        "capability_tag": "retrieve",
                        "candidate_capabilities": ["retrieve", "extract"],
                        "node_type": "tool_call",
                        "task_type": "tool_call",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:evidence", "non_empty_values"],
                    },
                    {
                        "id": "n2",
                        "description": "Extract structured fields from content.",
                        "dependencies": ["n1"],
                        "capability_tag": "extract",
                        "candidate_capabilities": ["extract", "reason"],
                        "node_type": "extraction",
                        "task_type": "extraction",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:extracted", "non_empty_values"],
                    },
                    {
                        "id": "final_response",
                        "description": "Return extraction result as final answer.",
                        "dependencies": ["n2"],
                        "capability_tag": "synthesize",
                        "candidate_capabilities": ["synthesize", "extract"],
                        "node_type": "final_response",
                        "task_type": "final_response",
                        "output_mode": "text",
                        "answer_role": "final",
                        "constraint": ["required_keys:answer", "non_empty_values"],
                    },
                ]
            }

        if family == "structured_generation":
            return {
                "nodes": [
                    {
                        "id": "n1",
                        "description": "Retrieve facts needed for structured generation.",
                        "inputs": {"query": query},
                        "dependencies": [],
                        "capability_tag": "retrieve",
                        "candidate_capabilities": ["retrieve", "extract"],
                        "node_type": "tool_call",
                        "task_type": "tool_call",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:evidence", "non_empty_values"],
                    },
                    {
                        "id": "n2",
                        "description": "Transform evidence into structured fields.",
                        "dependencies": ["n1"],
                        "capability_tag": "extract",
                        "candidate_capabilities": ["extract", "reason"],
                        "node_type": "extraction",
                        "task_type": "extraction",
                        "output_mode": "json",
                        "answer_role": "intermediate",
                        "constraint": ["required_keys:extracted", "non_empty_values"],
                    },
                    {
                        "id": "final_response",
                        "description": "Return final structured answer.",
                        "dependencies": ["n2"],
                        "capability_tag": "synthesize",
                        "candidate_capabilities": ["synthesize", "extract"],
                        "node_type": "final_response",
                        "task_type": "final_response",
                        "output_mode": "text",
                        "answer_role": "final",
                        "constraint": ["required_keys:answer", "non_empty_values"],
                    },
                ]
            }

        return {
            "nodes": [
                {
                    "id": "n1",
                    "description": "Retrieve evidence relevant to the question.",
                    "inputs": {"query": query},
                    "dependencies": [],
                    "capability_tag": "retrieve",
                    "candidate_capabilities": ["retrieve", "extract"],
                    "node_type": "tool_call",
                    "task_type": "tool_call",
                    "output_mode": "json",
                    "answer_role": "intermediate",
                    "constraint": ["required_keys:evidence", "non_empty_values"],
                },
                {
                    "id": "n2",
                    "description": "Extract core answer facts from evidence.",
                    "dependencies": ["n1"],
                    "capability_tag": "extract",
                    "candidate_capabilities": ["extract", "reason"],
                    "node_type": "extraction",
                    "task_type": "extraction",
                    "output_mode": "json",
                    "answer_role": "intermediate",
                    "constraint": ["required_keys:ceo", "non_empty_values"],
                },
                {
                    "id": "final_response",
                    "description": "Generate final answer for the user.",
                    "dependencies": ["n2"],
                    "capability_tag": "synthesize",
                    "candidate_capabilities": ["synthesize", "reason"],
                    "node_type": "final_response",
                    "task_type": "final_response",
                    "output_mode": "text",
                    "answer_role": "final",
                    "constraint": ["required_keys:answer", "non_empty_values"],
                },
            ]
        }

    def plan(self, context: PlanningContext) -> TaskTree:
        context.task_family = context.task_family or self.infer_task_family(context.user_query)
        if context.task_family == "plan":
            plan_focus = self.infer_plan_focus(context.user_query)
            if plan_focus:
                context.metadata["plan_focus"] = plan_focus
        if context.task_family == "plan":
            normalized = self.normalize_planner_output(self._default_plan(context))
            tree = TaskTree.from_dict(normalized)
            tree.metadata["task_family"] = context.task_family
            if context.metadata.get("plan_focus"):
                tree.metadata["plan_focus"] = context.metadata.get("plan_focus")
            self.ensure_final_node(tree)
            return self.attach_intent_specs(tree=tree, context=context)

        if self.llm_planner is None:
            normalized = self.normalize_planner_output(self._default_plan(context))
            tree = TaskTree.from_dict(normalized)
            tree.metadata["task_family"] = context.task_family
            if context.metadata.get("plan_focus"):
                tree.metadata["plan_focus"] = context.metadata.get("plan_focus")
            self.ensure_final_node(tree)
            return self.attach_intent_specs(tree=tree, context=context)

        raw = self.llm_planner(self._build_prompt(context))
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            raise TypeError("llm_planner must return a JSON string or dict.")
        normalized = self.normalize_planner_output(data)
        if context.task_family == "plan":
            node_ids = [str(node.get("id", "")) for node in normalized.get("nodes", [])]
            has_day_nodes = any(re.search(r"\bday\s*[1-9]\b", node_id.lower()) for node_id in node_ids)
            if not has_day_nodes:
                normalized = self.normalize_planner_output(self._default_plan(context))
        tree = TaskTree.from_dict(normalized)
        tree.metadata["task_family"] = context.task_family
        if context.metadata.get("plan_focus"):
            tree.metadata["plan_focus"] = context.metadata.get("plan_focus")
        self.ensure_final_node(tree)
        return self.attach_intent_specs(tree=tree, context=context)

    def refine_plan(self, tree: TaskTree, feedback: Dict[str, Any]) -> TaskTree:
        tree.metadata["refine_feedback"] = feedback
        tree.version += 1
        return tree

    def attach_intent_specs(self, tree: TaskTree, context: PlanningContext) -> TaskTree:
        for node in tree.nodes.values():
            if node.spec.intent is None:
                node.spec.intent = self.infer_intent_from_description(node=node)
            if not node.spec.intent_tags:
                node.spec.intent_tags = [node.spec.capability_tag, node.spec.task_type]
        self.attach_auto_constraints(
            tree=tree,
            task_family=context.task_family or str(tree.metadata.get("task_family", "")),
        )
        self.propagate_intents(tree)
        self.ensure_final_node(tree)
        return tree

    def attach_auto_constraints(self, tree: TaskTree, task_family: str = "") -> None:
        plan_mode = task_family.strip().lower() == "plan"
        for node in tree.nodes.values():
            existing_types = {getattr(c, "constraint_type", "") for c in node.spec.constraints}

            if node.is_final_response():
                if "final_structure" not in existing_types:
                    node.spec.constraints.append(
                        FinalStructureConstraint(
                            required_sections=["goal", "deliverable", "metric"] if plan_mode else [],
                            min_items=7 if plan_mode else 0,
                            forbid_query_echo=True,
                        )
                    )
                if "intent_alignment" not in existing_types:
                    node.spec.constraints.append(
                        IntentAlignmentConstraint(target_goal=node.spec.description)
                    )
                if plan_mode and "non_trivial_transform" not in existing_types:
                    node.spec.constraints.append(
                        NonTrivialTransformationConstraint(
                            input_field="query",
                            output_field="answer",
                            similarity_threshold=0.9,
                        )
                    )

            if node.spec.task_type == "extraction" and "non_empty_extraction" not in existing_types:
                node.spec.constraints.append(NonEmptyExtractionConstraint())

            if node.spec.task_type == "calculation" and "no_internal_error" not in existing_types:
                node.spec.constraints.append(NoInternalErrorConstraint())

            if node.spec.task_type in {"tool_call", "retrieval"} and "non_trivial_transform" not in existing_types:
                node.spec.constraints.append(
                    NonTrivialTransformationConstraint(
                        input_field="query",
                        output_field="evidence",
                        similarity_threshold=0.9,
                    )
                )

            if node.id == "verify_coverage":
                if "coverage_constraint" not in existing_types:
                    node.spec.constraints.append(CoverageConstraint())
                if "all_days_present" not in existing_types:
                    node.spec.constraints.append(AllDaysPresentConstraint())
                if "plan_topic_coverage" not in existing_types:
                    node.spec.constraints.append(PlanTopicCoverageConstraint())
                if "no_template_placeholder" not in existing_types:
                    node.spec.constraints.append(NoTemplatePlaceholderConstraint())

            if plan_mode and node.id in {"analyze_requirements", "design_plan_schema"}:
                if "intent_alignment" not in existing_types:
                    node.spec.constraints.append(IntentAlignmentConstraint(target_goal=node.spec.description))
                if node.id == "analyze_requirements" and "non_trivial_transform" not in existing_types:
                    node.spec.constraints.append(
                        NonTrivialTransformationConstraint(
                            input_field="query",
                            output_field="topics",
                            similarity_threshold=0.9,
                        )
                    )
                if node.id == "design_plan_schema" and "no_template_placeholder" not in existing_types:
                    node.spec.constraints.append(NoTemplatePlaceholderConstraint())

            if plan_mode and node.id.startswith("generate_day"):
                if "intent_alignment" not in existing_types:
                    node.spec.constraints.append(IntentAlignmentConstraint(target_goal=node.spec.description))
                if "no_template_placeholder" not in existing_types:
                    node.spec.constraints.append(NoTemplatePlaceholderConstraint())

            if plan_mode and node.is_final_response() and "no_template_placeholder" not in existing_types:
                node.spec.constraints.append(NoTemplatePlaceholderConstraint())

    def propagate_intents(self, tree: TaskTree) -> None:
        task_family = str(tree.metadata.get("task_family", "")).strip().lower()
        children_map: Dict[str, List[str]] = {node_id: [] for node_id in tree.nodes}
        for child_id, child in tree.nodes.items():
            for dep in child.dependencies:
                children_map.setdefault(dep, []).append(child_id)

        for node_id, node in tree.nodes.items():
            intent = node.spec.intent
            if intent is None:
                continue

            child_ids = children_map.get(node_id, [])
            if intent.propagates_to_children and intent.goal.strip():
                for child_id in child_ids:
                    child = tree.nodes.get(child_id)
                    if child is None or child.spec.intent is None:
                        continue
                    goal = intent.goal.strip()
                    if goal not in child.spec.intent.required_upstream_intents:
                        child.spec.intent.required_upstream_intents.append(goal)

            # final_response goal reversely constrains upstream synthesis / aggregation path.
            if node.is_final_response() and intent.goal.strip():
                for dep_id in node.dependencies:
                    dep = tree.nodes.get(dep_id)
                    if dep is None or dep.spec.intent is None:
                        continue
                    if dep.is_aggregation_node() or dep.spec.task_type in {"reasoning", "synthesis"}:
                        criterion = f"supports_final_goal:{intent.goal.strip()}"
                        if criterion not in dep.spec.intent.child_completion_criteria:
                            dep.spec.intent.child_completion_criteria.append(criterion)

            # compare nodes require at least two upstream objects.
            if node.spec.task_type == "comparison":
                if "requires_two_upstream_objects" not in intent.child_completion_criteria:
                    intent.child_completion_criteria.append("requires_two_upstream_objects")
                if len(node.dependencies) < 2:
                    node.metadata["intent_warning"] = "comparison_requires_at_least_two_inputs"

            # summary nodes require evidence-bearing upstream outputs.
            if node.spec.task_type == "summarization":
                if node.dependencies and "requires_evidence_bearing_upstream" not in intent.required_upstream_intents:
                    intent.required_upstream_intents.append("requires_evidence_bearing_upstream")

            # plan family expects day-by-day coverage.
            if task_family == "plan" and (node.is_final_response() or "plan" in intent.goal.lower()):
                for day in range(1, 8):
                    criterion = f"cover_day_{day}"
                    if criterion not in intent.child_completion_criteria:
                        intent.child_completion_criteria.append(criterion)
                for section in ["goal", "deliverable", "metric"]:
                    criterion = f"include_section_{section}"
                    if criterion not in intent.child_completion_criteria:
                        intent.child_completion_criteria.append(criterion)

    def ensure_final_node(self, tree: TaskTree) -> None:
        sink_ids = tree.get_sink_nodes()
        if not sink_ids:
            tree.ensure_single_final_response()
            tree.validate()
            return

        final_sink_ids = [node_id for node_id in sink_ids if tree.nodes[node_id].is_final_response()]
        if len(sink_ids) != 1 or len(final_sink_ids) != 1:
            tree.ensure_single_final_response()
            tree.validate()

    def infer_intent_from_description(self, node: TaskNode) -> IntentSpec:
        text = node.spec.description.lower()
        task_type = node.spec.task_type
        success_conditions: List[str] = []
        evidence_requirements: List[str] = []
        output_semantics: Dict[str, str] = {}
        aggregation_requirements: List[str] = []
        child_completion_criteria: List[str] = []

        if task_type == "tool_call":
            success_conditions.extend(["evidence_retrieved"])
            output_semantics["evidence"] = "retrieved supporting context"
        if task_type == "extraction":
            success_conditions.extend(["fields_extracted"])
            output_semantics["extracted"] = "structured fields extracted from evidence"
        if task_type == "summarization":
            success_conditions.extend(["summary_generated"])
            output_semantics["summary"] = "concise summary"
            aggregation_requirements.append("cover_all_upstream_nodes")
        if task_type == "comparison":
            success_conditions.extend(["comparison_completed"])
            output_semantics["comparison"] = "comparison result across candidates"
            aggregation_requirements.append("include_all_compared_items")
            child_completion_criteria.append("requires_two_upstream_objects")
        if task_type == "calculation":
            success_conditions.extend(["calculation_completed"])
            output_semantics["result"] = "numeric calculation result"
        if task_type == "verification":
            success_conditions.extend(["coverage_verified"])
            output_semantics["coverage_ok"] = "whether all day entries satisfy constraints"
            output_semantics["missing_days"] = "list of missing day indexes"
            output_semantics["missing_fields"] = "list of missing required fields"
            output_semantics["semantic_gaps"] = "semantic quality gaps"
            output_semantics["grounded_nodes"] = "nodes used for verification"
        if task_type == "aggregation" and "day" in text:
            success_conditions.extend(["day_plan_generated"])
            output_semantics["day"] = "day index"
            output_semantics["goal"] = "day objective"
            output_semantics["deliverable"] = "day deliverable"
            output_semantics["metric"] = "day metric"
        if task_type == "final_response":
            success_conditions.extend(["final_response_generated"])
            output_semantics["answer"] = "final user-facing answer"
            aggregation_requirements.append("grounded_in_upstream_outputs")
            evidence_requirements.append("dependency_outputs")
            child_completion_criteria.append("must_reference_used_nodes")

        if "source" in text or "factual" in text:
            evidence_requirements.append("source")

        response_style = "plain"
        if node.spec.output_mode == "table":
            response_style = "tabular"
        elif node.spec.output_mode == "json":
            response_style = "structured_json"

        return IntentSpec(
            goal=node.spec.description,
            success_conditions=success_conditions,
            evidence_requirements=evidence_requirements,
            output_semantics=output_semantics,
            response_style=response_style,
            aggregation_requirements=aggregation_requirements,
            child_completion_criteria=child_completion_criteria,
        )

    def build_patch_candidates(self, node: TaskNode, failure: Dict[str, Any]) -> List[Dict[str, Any]]:
        reasons = " ".join(failure.get("reasons", [])).lower()
        candidates: List[Dict[str, Any]] = [{"template": "retry_same", "score": 0.2}]
        if node.spec.task_type in {"final_response"}:
            candidates.append({"template": "final_response_patch", "score": 0.95})
        if node.spec.task_type in {"summarization"}:
            candidates.append({"template": "summary_patch", "score": 0.85})
        if node.spec.task_type in {"comparison"}:
            candidates.append({"template": "compare_patch", "score": 0.85})
        if node.spec.task_type in {"calculation"}:
            candidates.append({"template": "calculation_patch", "score": 0.8})

        if "semantic" in reasons or "factual" in reasons or "source" in reasons:
            candidates.append({"template": "evidence_retrieval", "score": 0.9})
            candidates.append({"template": "crosscheck", "score": 0.6})
        if "schema" in reasons or "required" in reasons:
            candidates.append({"template": "normalization", "score": 0.7})
        return sorted(candidates, key=lambda x: x["score"], reverse=True)

    def replan_subtree(
        self,
        tree: TaskTree,
        failed_node_id: str,
        context: PlanningContext,
    ) -> List[TaskNode]:
        if failed_node_id not in tree.nodes:
            return []

        source = tree.nodes[failed_node_id]
        replacement = TaskNode(
            id=source.id,
            spec=source.spec,
            dependencies=list(source.dependencies),
            assigned_agent=source.assigned_agent,
            fallback_agents=list(source.fallback_agents),
            status="pending",
            inputs=dict(source.inputs),
            outputs={},
            execution_policy=source.execution_policy,
            metadata={**source.metadata, "replanned": True},
            parent_intent_ids=list(source.parent_intent_ids),
            intent_status="unknown",
            repair_history=list(source.repair_history),
        )
        return [replacement]
