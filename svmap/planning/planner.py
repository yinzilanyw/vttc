
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from svmap.models import IntentSpec, TaskNode, TaskTree


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
        if any(k in text for k in ["summarize", "summary", "tl;dr", "概括", "总结"]):
            return "summary"
        if any(k in text for k in ["compare", "difference", "vs", "versus", "对比", "比较"]):
            return "compare"
        if any(k in text for k in ["calculate", "compute", "total", "sum", "multiply", "plus", "减", "加", "乘", "除"]):
            return "calculate"
        if any(k in text for k in ["extract", "fields", "json", "结构化", "提取"]):
            return "extract"
        return "qa"

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
        return (
            "Generate a task DAG in JSON only.\n"
            f"User query: {context.user_query}\n"
            f"Task family: {family}\n"
            f"Available agents: {context.available_agents}\n"
            f"Global goal: {context.global_goal}\n"
            f"Global constraints: {context.global_constraints}\n\n"
            f"{self.build_task_taxonomy_prompt()}\n"
            "Output must follow the schema and include compact dependencies.\n"
        )

    def _default_plan(self, context: PlanningContext) -> Dict[str, Any]:
        family = context.task_family or self.infer_task_family(context.user_query)
        query = context.user_query
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
        if self.llm_planner is None:
            normalized = self.normalize_planner_output(self._default_plan(context))
            tree = TaskTree.from_dict(normalized)
            tree.metadata["task_family"] = context.task_family
            return self.attach_intent_specs(tree=tree, context=context)

        raw = self.llm_planner(self._build_prompt(context))
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            raise TypeError("llm_planner must return a JSON string or dict.")
        normalized = self.normalize_planner_output(data)
        tree = TaskTree.from_dict(normalized)
        tree.metadata["task_family"] = context.task_family
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
        tree.ensure_single_final_response()
        return tree

    def infer_intent_from_description(self, node: TaskNode) -> IntentSpec:
        text = node.spec.description.lower()
        task_type = node.spec.task_type
        success_conditions: List[str] = []
        evidence_requirements: List[str] = []
        output_semantics: Dict[str, str] = {}
        aggregation_requirements: List[str] = []

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
        if task_type == "calculation":
            success_conditions.extend(["calculation_completed"])
            output_semantics["result"] = "numeric calculation result"
        if task_type == "final_response":
            success_conditions.extend(["final_response_generated"])
            output_semantics["answer"] = "final user-facing answer"
            aggregation_requirements.append("grounded_in_upstream_outputs")
            evidence_requirements.append("dependency_outputs")

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
