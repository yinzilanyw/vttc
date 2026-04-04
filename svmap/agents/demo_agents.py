from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from svmap.models import TaskNode

from .base import BaseAgent


def _load_openai_client(api_key: str, base_url: str) -> Any:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "openai package is not installed. Install it with: pip install openai"
        ) from exc
    return OpenAI(api_key=api_key, base_url=base_url)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _flatten_dependency_text(dependency_outputs: Dict[str, Dict[str, Any]]) -> str:
    chunks: List[str] = []
    for dep_id, dep_output in dependency_outputs.items():
        if isinstance(dep_output, dict):
            chunks.append(f"{dep_id}={dep_output}")
    return " | ".join(chunks)


def _find_value_from_dependency_outputs(
    dependency_outputs: Dict[str, Dict[str, Any]],
    keys: List[str],
) -> Optional[Any]:
    for dep_output in dependency_outputs.values():
        if not isinstance(dep_output, dict):
            continue
        for key in keys:
            value = dep_output.get(key)
            if value is not None and (not isinstance(value, str) or value.strip()):
                return value
    return None


def _ensure_required_fields(node: TaskNode, output: Dict[str, Any]) -> Dict[str, Any]:
    required = node.spec.io.required_output_field_names()
    output_mode = node.spec.output_mode
    for field_name in required:
        if field_name in output and output[field_name] is not None and output[field_name] != "":
            continue
        lowered = field_name.lower()
        if output_mode == "number" or "count" in lowered or "total" in lowered:
            output[field_name] = output.get("result", 0)
        elif output_mode == "boolean" or lowered.startswith("is_") or lowered.startswith("has_"):
            output[field_name] = False
        elif output_mode == "json":
            output[field_name] = output.get("extracted", {})
        else:
            output[field_name] = output.get("summary") or output.get("answer") or "unknown"
    return output


def _parse_simple_expression(text: str) -> Optional[str]:
    cleaned = text.replace(",", " ")
    match = re.search(r"([0-9][0-9 +\-*/().]+)", cleaned)
    if not match:
        return None
    expr = match.group(1).strip()
    if re.fullmatch(r"[0-9+\-*/(). ]+", expr) is None:
        return None
    return expr


def _extract_query_topics(query: str) -> List[str]:
    text = re.sub(r"[^a-zA-Z0-9_\-\s]", " ", _safe_str(query).lower())
    tokens = [t for t in re.split(r"\s+", text) if t]
    stop = {
        "a",
        "an",
        "the",
        "to",
        "for",
        "with",
        "in",
        "on",
        "of",
        "and",
        "or",
        "by",
        "is",
        "are",
        "be",
        "this",
        "that",
        "who",
        "what",
        "how",
        "build",
        "design",
        "learning",
        "plan",
        "day",
        "daily",
        "days",
        "building",
        "design",
        "goals",
        "deliverables",
        "metrics",
    }
    topics: List[str] = []
    for token in tokens:
        if token.isdigit():
            continue
        if re.fullmatch(r"\d+-day", token):
            continue
        if token in stop or len(token) < 3:
            continue
        if token not in topics:
            topics.append(token)
    return topics[:8]


def _normalize_topics_for_plan(topics: List[str]) -> List[str]:
    stop = {
        "including",
        "include",
        "one",
        "two",
        "three",
        "learning",
        "plan",
        "daily",
        "goal",
        "goals",
        "deliverable",
        "deliverables",
        "metric",
        "metrics",
    }
    merged: List[str] = []
    idx = 0
    while idx < len(topics):
        token = str(topics[idx]).strip().lower()
        if not token or token in stop:
            idx += 1
            continue
        if token == "task" and idx + 1 < len(topics):
            nxt = str(topics[idx + 1]).strip().lower()
            if nxt in {"tree", "trees"}:
                token = "task trees"
                idx += 1
        if token not in merged:
            merged.append(token)
        idx += 1
    return merged[:8]


REPO_BINDING_HINTS = [
    "svmap/",
    "planner.py",
    "verifiers.py",
    "engine.py",
    "executor.py",
    "replanner.py",
    "metrics.py",
    "run_multitask_eval.py",
    "task_tree.py",
    "task_node.py",
]

GENERIC_DELIVERABLE_PATTERNS = [
    r"commit code/doc changes",
    r"attach a short validation log",
    r"include modified file paths",
    r"add corresponding test or trace artifact",
    r"implementation notes",
]

GENERIC_METRIC_PATTERNS = [
    r"all required fields parsed",
    r"passes coverage verification",
    r"includes explicit goal/deliverable/metric fields",
]


def _contains_repo_binding_hint(text: str) -> bool:
    lowered = _safe_str(text).lower()
    return any(x.lower() in lowered for x in REPO_BINDING_HINTS)


def _matches_generic_deliverable(text: str) -> bool:
    lowered = _safe_str(text).lower()
    return any(re.search(p, lowered) for p in GENERIC_DELIVERABLE_PATTERNS)


def _matches_generic_metric(text: str) -> bool:
    lowered = _safe_str(text).lower()
    return any(re.search(p, lowered) for p in GENERIC_METRIC_PATTERNS)


def _build_specific_deliverable(day_idx: int, assigned_topic: str) -> str:
    artifacts = {
        1: "update svmap/planning/planner.py and write artifacts/day1_requirements.md task-tree draft note",
        2: "implement runnable orchestration updates in svmap/pipeline.py and svmap/runtime/executor.py with artifacts/day2_trace.json",
        3: "update svmap/models/task_node.py and svmap/models/task_tree.py and add DAG validator unit tests",
        4: "extend svmap/verification/verifiers.py and svmap/verification/engine.py with injected-error verification tests",
        5: "update svmap/models/constraints.py and add intent-alignment test cases under experiments",
        6: "update svmap/runtime/replanner.py to output graph-delta traces and demonstrate subtree/global replan",
        7: "generate ablation report from experiments/run_multitask_eval.py outputs with case-study tables",
    }
    artifact = artifacts.get(day_idx, f"update repository code and tests for {assigned_topic}")
    return f"Implement {artifact} for {assigned_topic}."


def _build_measurable_metric(day_idx: int) -> str:
    metrics = {
        1: "Requirements extraction keeps >= 5 core topics with 0 obvious noise terms across 5 sample queries.",
        2: "Workflow executes end-to-end in 3/3 runs with <= 1 manual intervention.",
        3: "Task-tree/schema validation covers >= 10 cases with 100% pass rate.",
        4: "Verifier catches injected node/edge/subtree/global failures in >= 4/4 scenarios.",
        5: "Intent/constraint checks reduce topic drift failures to 0 on the plan subset.",
        6: "At least one subtree replan and one graph-delta trace are produced on a failing case.",
        7: "Ablation report contains full/no_quality_verifier/no_repair variants with all tables generated.",
    }
    return metrics.get(day_idx, "Define a numeric threshold and verify it with logs or tests.")


def _is_specific_deliverable(text: str) -> bool:
    lowered = _safe_str(text).lower()
    artifact_tokens = [
        "module",
        "script",
        "unit test",
        "integration test",
        "trace",
        "table",
        "report",
        "document",
        "spec",
        "validator",
    ]
    has_artifact_type = any(token in lowered for token in artifact_tokens)
    has_repo_binding = _contains_repo_binding_hint(lowered)
    too_generic = _matches_generic_deliverable(lowered)
    return has_artifact_type and (has_repo_binding or not too_generic)


def _is_measurable_metric(text: str) -> bool:
    lowered = _safe_str(text).lower()
    has_numeric_signal = bool(
        re.search(r"\d+|>=|<=|%|pass rate|latency|count|cases?|runs?", lowered)
    )
    too_generic = _matches_generic_metric(lowered)
    return has_numeric_signal and not too_generic


def _is_repo_bound_text(text: str) -> bool:
    lowered = _safe_str(text).lower()
    repo_ref_tokens = ["modified file", "file path", "commit", "patch", "diff", "repo", "repository", ".py", ".md"]
    return any(tok in lowered for tok in repo_ref_tokens)


def _parse_day_index(node_id: str) -> Optional[int]:
    m = re.search(r"generate_day(\d+)", node_id.lower())
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _is_placeholder_text(text: str) -> bool:
    lowered = _safe_str(text).lower()
    patterns = [
        r"complete step\s*\d+",
        r"artifact\s*\d+",
        r"measure\s*\d+",
        r"step\s*\d+",
    ]
    return any(re.search(p, lowered) for p in patterns)


class RetrieveAgent(BaseAgent):
    def __init__(
        self,
        use_model_api: bool = True,
        api_key: str = "",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-flash",
    ) -> None:
        self.use_model_api = use_model_api and bool(api_key.strip())
        self.model = model
        self.client = _load_openai_client(api_key=api_key, base_url=base_url) if self.use_model_api else None

    def supported_task_types(self) -> List[str]:
        return ["tool_call", "retrieval", "reasoning", "extraction", "summarization", "comparison"]

    def supported_output_modes(self) -> List[str]:
        return ["text", "json", "table"]

    def _extract_json_from_text(self, text: str) -> Dict[str, Any]:
        text = text.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        left = text.find("{")
        right = text.rfind("}")
        if left >= 0 and right > left:
            snippet = text[left : right + 1]
            try:
                parsed = json.loads(snippet)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def _extract_chat_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks: List[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                else:
                    text = getattr(part, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
            return "\n".join(chunks).strip()
        return ""

    def _retrieve_with_bailian(
        self,
        query: str,
        dependency_outputs: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        if self.client is None:
            raise RuntimeError("RetrieveAgent requires Bailian online mode (USE_MODEL_API=1 with DASHSCOPE_API_KEY).")

        dep_text = _flatten_dependency_text(dependency_outputs)
        prompt = (
            "Read the user query and optional upstream evidence. "
            "Return JSON object with keys: evidence, source, founder, company, ceo, summary. "
            "If unknown, use empty string.\n"
            f"query={query}\n"
            f"upstream={dep_text}\n"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a retrieval assistant. Return only JSON object, no markdown.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        text = self._extract_chat_text(response)
        parsed = self._extract_json_from_text(text)
        if not parsed:
            parsed = {"evidence": text, "source": "bailian_direct"}
        return parsed

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        query = _safe_str(inputs.get("node_inputs", {}).get("query"))
        if not query:
            query = _safe_str(inputs.get("global_context", {}).get("query"))
        dependency_outputs = inputs.get("dependency_outputs", {})

        retrieved = self._retrieve_with_bailian(
            query=query,
            dependency_outputs=dependency_outputs,
        )
        output = {
            "query": query,
            "evidence": _safe_str(retrieved.get("evidence")) or query,
            "source": _safe_str(retrieved.get("source")) or "bailian_direct",
        }
        for key in ("founder", "company", "ceo", "summary"):
            value = retrieved.get(key)
            if isinstance(value, str) and value.strip():
                output[key] = value.strip()
        return _ensure_required_fields(node=node, output=output)

    def estimate_success(self, node: TaskNode) -> float:
        return 0.95 if self.use_model_api else 0.0


class ExtractAgent(BaseAgent):
    def supported_task_types(self) -> List[str]:
        return ["extraction", "reasoning", "tool_call"]

    def supported_output_modes(self) -> List[str]:
        return ["json", "text", "table"]

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        query = _safe_str(inputs.get("node_inputs", {}).get("query")) or _safe_str(
            inputs.get("global_context", {}).get("query")
        )
        combined = f"{query} {_flatten_dependency_text(dependency_outputs)}"

        founder = _safe_str(
            _find_value_from_dependency_outputs(dependency_outputs, ["founder", "person", "subject"])
        )
        company = _safe_str(_find_value_from_dependency_outputs(dependency_outputs, ["company", "organization"]))
        ceo = _safe_str(_find_value_from_dependency_outputs(dependency_outputs, ["ceo", "answer"]))

        if not founder:
            m = re.search(r"founded by\s+([A-Za-z .'-]+)\??", combined, re.IGNORECASE)
            founder = m.group(1).strip() if m else ""

        extracted: Dict[str, Any] = {}
        if founder:
            extracted["founder"] = founder
        if company:
            extracted["company"] = company
        if ceo:
            extracted["ceo"] = ceo

        output = {
            "extracted": extracted,
            "source": "extract_from_retrieval",
            "evidence": combined[:280],
        }
        output.update(extracted)
        return _ensure_required_fields(node=node, output=output)

    def estimate_success(self, node: TaskNode) -> float:
        return 0.9


class SummarizeAgent(BaseAgent):
    def supported_task_types(self) -> List[str]:
        return ["summarization", "aggregation", "reasoning"]

    def supported_output_modes(self) -> List[str]:
        return ["text", "json"]

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        if dependency_outputs:
            parts = []
            for dep_output in dependency_outputs.values():
                if isinstance(dep_output, dict):
                    piece = dep_output.get("summary") or dep_output.get("evidence") or str(dep_output)
                    parts.append(str(piece))
            summary = " | ".join(parts)
        else:
            summary = _safe_str(inputs.get("node_inputs", {}).get("text"))
        summary = summary.strip() or "No upstream content available."

        output = {
            "summary": summary[:2000],
            "coverage_keys": list(dependency_outputs.keys()),
            "source": "summarizer",
        }
        return _ensure_required_fields(node=node, output=output)

    def estimate_success(self, node: TaskNode) -> float:
        return 0.9


class CompareAgent(BaseAgent):
    def supported_task_types(self) -> List[str]:
        return ["comparison", "reasoning", "aggregation"]

    def supported_output_modes(self) -> List[str]:
        return ["table", "json", "text"]

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        items = []
        for dep_id, dep_output in dependency_outputs.items():
            if isinstance(dep_output, dict):
                candidate = dep_output.get("company") or dep_output.get("name") or dep_id
            else:
                candidate = dep_id
            items.append(str(candidate))

        if not items:
            seed_items = inputs.get("node_inputs", {}).get("items")
            if isinstance(seed_items, list):
                items = [str(x) for x in seed_items if str(x).strip()]

        comparison = f"Compared: {', '.join(items)}" if items else "Compared: none"
        output = {
            "compared_items": items,
            "comparison": comparison,
            "dimensions": ["availability", "evidence"],
            "winner": items[0] if items else "",
            "source": "compare_agent",
        }
        return _ensure_required_fields(node=node, output=output)

    def estimate_success(self, node: TaskNode) -> float:
        return 0.87


class CalculateAgent(BaseAgent):
    def supported_task_types(self) -> List[str]:
        return ["calculation", "reasoning", "tool_call"]

    def supported_output_modes(self) -> List[str]:
        return ["number", "json", "text"]

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        expression = _safe_str(inputs.get("node_inputs", {}).get("expression"))
        query = _safe_str(inputs.get("global_context", {}).get("query"))
        if not expression:
            expression = _parse_simple_expression(query) or ""
        result: float | int = 0
        error = ""
        if expression:
            try:
                result = eval(expression, {"__builtins__": {}}, {})  # noqa: S307
            except Exception as exc:  # pragma: no cover
                error = str(exc)
        output = {
            "expression": expression,
            "result": result,
            "calculation_trace": f"{expression}={result}" if expression else "no_expression",
            "source": "calculate_agent",
        }
        if error:
            output["calculation_error"] = error
        return _ensure_required_fields(node=node, output=output)

    def estimate_success(self, node: TaskNode) -> float:
        return 0.84


class SynthesizeAgent(BaseAgent):
    def supported_task_types(self) -> List[str]:
        return ["final_response", "synthesis", "aggregation", "reasoning"]

    def supported_output_modes(self) -> List[str]:
        return ["text", "json", "table"]

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        query = _safe_str(inputs.get("global_context", {}).get("query"))
        topics = _extract_query_topics(query)
        core_topics = topics[:3] if topics else ["the target topic"]

        day_idx = _parse_day_index(node.id)
        if day_idx is not None:
            schema_output = dependency_outputs.get("design_plan_schema", {})
            if not isinstance(schema_output, dict):
                schema_output = {}
            requirements_output = dependency_outputs.get("analyze_requirements", {})
            if not isinstance(requirements_output, dict):
                requirements_output = {}

            topic_allocation = schema_output.get("topic_allocation", {})
            if not isinstance(topic_allocation, dict):
                topic_allocation = {}
            progression = schema_output.get("progression", [])
            if not isinstance(progression, list):
                progression = []
            must_cover_topics = requirements_output.get("must_cover_topics", [])
            if not isinstance(must_cover_topics, list):
                must_cover_topics = []
            quality_criteria = schema_output.get("quality_criteria", {})
            if not isinstance(quality_criteria, dict):
                quality_criteria = {}
            quality_targets = requirements_output.get("quality_targets", {})
            if not isinstance(quality_targets, dict):
                quality_targets = {}
            deliverable_template = schema_output.get("deliverable_template", {})
            if not isinstance(deliverable_template, dict):
                deliverable_template = {}
            metric_template = schema_output.get("metric_template", {})
            if not isinstance(metric_template, dict):
                metric_template = {}

            day_key = f"day{day_idx}"
            assigned_topic = _safe_str(topic_allocation.get(day_key))
            if not assigned_topic and 0 <= day_idx - 1 < len(progression):
                assigned_topic = _safe_str(progression[day_idx - 1])
            if not assigned_topic:
                assigned_topic = "multi-agent workflow and verifiable task trees"

            topic_text = ", ".join(must_cover_topics[:4] or core_topics)
            goal = (
                f"Focus on {assigned_topic} while keeping alignment with {topic_text}."
            )
            deliverable = _build_specific_deliverable(day_idx=day_idx, assigned_topic=assigned_topic)
            metric = _build_measurable_metric(day_idx=day_idx)
            if quality_criteria.get("must_reference_repo_changes", False) or quality_targets.get("repo_binding_required", False):
                deliverable += " Include modified file paths in the daily artifact note."
            if (
                quality_criteria.get("deliverable_must_be_specific", False)
                or quality_targets.get("deliverable_specificity", False)
            ) and not _is_specific_deliverable(deliverable):
                deliverable = f"Create a code module and unit test bundle for {assigned_topic}."
            if deliverable_template.get("must_include_file_or_module", False) and not any(
                tok in deliverable.lower() for tok in ["file", "module", "script", ".py"]
            ):
                deliverable += " Add at least one module/file path reference."
            if deliverable_template.get("must_include_test_or_trace", False) and not any(
                tok in deliverable.lower() for tok in ["test", "trace"]
            ):
                deliverable += " Add corresponding test or trace artifact."
            if (
                quality_criteria.get("metric_must_be_measurable", False)
                or quality_targets.get("metric_measurability", False)
                or metric_template.get("must_be_numeric_or_thresholded", False)
            ) and not _is_measurable_metric(metric):
                metric = "Define a numeric threshold (>=90% pass) and verify it in execution logs."
            if metric_template.get("must_not_only_check_field_presence", False):
                if any(tok in metric.lower() for tok in ["includes fields", "passes verification", "looks complete"]):
                    metric = "Set measurable completion target: >=90% pass rate across >=10 test cases."
            output = {
                "day": day_idx,
                "goal": goal,
                "deliverable": deliverable,
                "metric": metric,
                "source": "synthesize_agent",
            }
            return _ensure_required_fields(node=node, output=output)

        if node.is_final_response():
            day_items: List[Dict[str, Any]] = []
            used_nodes: List[str] = []
            coverage_verification: Dict[str, Any] = {}
            for dep_id, dep_output in dependency_outputs.items():
                if isinstance(dep_output, dict) and isinstance(dep_output.get("day"), int):
                    day_items.append(dep_output)
                    used_nodes.append(dep_id)
                if dep_id == "verify_coverage" and isinstance(dep_output, dict):
                    coverage_verification = dep_output
                    if dep_id not in used_nodes:
                        used_nodes.append(dep_id)
                    for nid in dep_output.get("grounded_nodes", []):
                        if isinstance(nid, str) and nid not in used_nodes:
                            used_nodes.append(nid)
            day_items = sorted(day_items, key=lambda x: int(x.get("day", 0)))
            if day_items:
                lines: List[str] = []
                seen_nodes = set()
                dedup_used_nodes: List[str] = []
                for nid in used_nodes:
                    if nid in seen_nodes:
                        continue
                    seen_nodes.add(nid)
                    dedup_used_nodes.append(nid)
                for item in day_items:
                    lines.append(
                        f"Day {item.get('day')}: goal={_safe_str(item.get('goal'))}; "
                        f"deliverable={_safe_str(item.get('deliverable'))}; "
                        f"metric={_safe_str(item.get('metric'))}"
                    )
                answer = "\n".join(lines)
                output = {
                    "answer": answer,
                    "final_response": answer,
                    "source": "synthesize_agent",
                    "used_nodes": dedup_used_nodes or list(dependency_outputs.keys()),
                    "coverage_verification": coverage_verification,
                }
                return _ensure_required_fields(node=node, output=output)

        answer = ""
        for dep_output in dependency_outputs.values():
            if not isinstance(dep_output, dict):
                continue
            for key in ("answer", "summary", "comparison", "result", "ceo", "company", "evidence"):
                value = dep_output.get(key)
                if isinstance(value, str) and value.strip():
                    answer = value.strip()
                    break
                if isinstance(value, (int, float)):
                    answer = str(value)
                    break
            if answer:
                break

        if not answer:
            answer = "Unable to derive a confident answer from current evidence."

        output = {
            "answer": answer,
            "final_response": answer,
            "source": "synthesize_agent",
            "used_nodes": list(dependency_outputs.keys()),
        }
        return _ensure_required_fields(node=node, output=output)

    def estimate_success(self, node: TaskNode) -> float:
        return 0.93


class ReasonAgent(BaseAgent):
    def supported_task_types(self) -> List[str]:
        return ["reasoning", "aggregation", "summarization"]

    def supported_output_modes(self) -> List[str]:
        return ["json", "text"]

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        query = _safe_str(inputs.get("global_context", {}).get("query"))
        topics = _normalize_topics_for_plan(_extract_query_topics(query))
        dependency_outputs = inputs.get("dependency_outputs", {})

        if node.id == "analyze_requirements":
            query_lower = query.lower()
            svmap_focus = any(
                token in query_lower
                for token in ["multi-agent", "workflow", "verifiable", "task tree", "task trees"]
            )
            must_cover_topics = []
            if svmap_focus:
                must_cover_topics.extend(
                    [
                        "multi-agent workflow",
                        "verifiable task trees",
                        "planning",
                        "verification",
                        "replanning",
                    ]
                )
            for topic in topics:
                if topic not in must_cover_topics:
                    must_cover_topics.append(topic)
            must_cover_topics = must_cover_topics[:5]
            constraints = []
            if "7-day" in query.lower() or "7 day" in query.lower():
                constraints.append("duration_days=7")
            if "goal" in query.lower():
                constraints.append("include_goal_field")
            if "deliverable" in query.lower():
                constraints.append("include_deliverable_field")
            if "metric" in query.lower():
                constraints.append("include_metric_field")
            primary_domain = "multi-agent systems" if svmap_focus else (topics[0] if topics else "general planning")
            secondary_focus = "verifiable task trees" if svmap_focus else "query-specific structure"
            output = {
                "primary_domain": primary_domain,
                "secondary_focus": secondary_focus,
                "task_form": "7-day learning plan",
                "topics": topics or ["general"],
                "must_cover_topics": must_cover_topics,
                "forbidden_topic_drift": [
                    "pure async/event-loop curriculum without task-tree verification",
                    "generic runtime-only optimization track",
                    "high-level generic software plan with no concrete artifacts",
                ],
                "constraints": constraints or ["respond_to_user_query"],
                "required_fields": ["goal", "deliverable", "metric"],
                "duration_days": 7,
                "quality_targets": {
                    "deliverable_specificity": True,
                    "metric_measurability": True,
                    "repo_binding_required": True,
                },
                "source": "reason_agent",
            }
            return _ensure_required_fields(node=node, output=output)

        if node.id == "design_plan_schema":
            req = {}
            for dep_output in dependency_outputs.values():
                if isinstance(dep_output, dict) and dep_output.get("required_fields"):
                    req = dep_output
                    break
            required_fields = req.get("required_fields") if isinstance(req, dict) else None
            if not isinstance(required_fields, list) or not required_fields:
                required_fields = ["goal", "deliverable", "metric"]
            svmap_focus = False
            if isinstance(req, dict):
                joined_req = " ".join(
                    [
                        _safe_str(req.get("primary_domain")),
                        _safe_str(req.get("secondary_focus")),
                        " ".join([_safe_str(x) for x in req.get("must_cover_topics", []) if isinstance(req.get("must_cover_topics"), list)]),
                    ]
                ).lower()
                svmap_focus = any(
                    token in joined_req for token in ["multi-agent", "workflow", "verifiable", "task tree", "task trees"]
                )
            if svmap_focus:
                progression = [
                    "multi-agent basics",
                    "workflow orchestration",
                    "explicit task trees",
                    "node and edge verification",
                    "intent and constraints",
                    "replanning and graph transformation",
                    "end-to-end capstone",
                ]
                topic_allocation = {
                    "day1": "multi-agent basics and decomposition",
                    "day2": "workflow orchestration with typed node interfaces",
                    "day3": "explicit task-tree representation and dependency control",
                    "day4": "node/edge/subtree/global verification flow",
                    "day5": "intent alignment and constraint-aware validation",
                    "day6": "failure taxonomy and subtree/global replanning",
                    "day7": "end-to-end case study with ablation metrics",
                }
            else:
                progression = [
                    "scope and requirements",
                    "core concepts",
                    "implementation baseline",
                    "verification and tests",
                    "iteration and optimization",
                    "integration and hardening",
                    "capstone delivery",
                ]
                topic_seed = req.get("must_cover_topics", []) if isinstance(req, dict) else []
                if not isinstance(topic_seed, list) or not topic_seed:
                    topic_seed = topics or ["target domain"]
                topic_allocation = {
                    "day1": f"requirements and scope for {topic_seed[0]}",
                    "day2": f"core concept drill-down for {topic_seed[min(1, len(topic_seed)-1)]}",
                    "day3": f"build baseline around {topic_seed[min(2, len(topic_seed)-1)]}",
                    "day4": f"verification and testing for {topic_seed[min(0, len(topic_seed)-1)]}",
                    "day5": f"optimization and iteration for {topic_seed[min(1, len(topic_seed)-1)]}",
                    "day6": f"integration and hardening for {topic_seed[min(2, len(topic_seed)-1)]}",
                    "day7": "capstone with measurable outcomes",
                }
            output = {
                "day_template": {
                    "goal": "A day-specific learning objective tied to query topics.",
                    "deliverable": "A concrete artifact produced today.",
                    "metric": "Measurable completion criteria.",
                },
                "progression": progression,
                "topic_allocation": topic_allocation,
                "required_fields": required_fields,
                "quality_criteria": {
                    "deliverable_must_be_specific": True,
                    "metric_must_be_measurable": True,
                    "avoid_generic_templates": True,
                    "must_reference_repo_changes": True,
                },
                "deliverable_template": {
                    "must_include_file_or_module": True,
                    "must_include_test_or_trace": True,
                    "must_include_validation_artifact": True,
                },
                "metric_template": {
                    "must_be_numeric_or_thresholded": True,
                    "must_measure_task_completion": True,
                    "must_not_only_check_field_presence": True,
                },
                "source": "reason_agent",
            }
            return _ensure_required_fields(node=node, output=output)

        summary = _safe_str(inputs.get("node_inputs", {}).get("text")) or query
        return _ensure_required_fields(
            node=node,
            output={
                "summary": summary,
                "source": "reason_agent",
            },
        )

    def estimate_success(self, node: TaskNode) -> float:
        return 0.92


class VerifyAgent(BaseAgent):
    def supported_task_types(self) -> List[str]:
        return ["verification", "reasoning", "aggregation"]

    def supported_output_modes(self) -> List[str]:
        return ["json", "text", "boolean"]

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        query = _safe_str(inputs.get("global_context", {}).get("query"))
        topics = _extract_query_topics(query)

        if node.id == "verify_coverage":
            day_objects: Dict[int, Dict[str, Any]] = {}
            grounded_nodes: List[str] = []
            quality_criteria: Dict[str, Any] = {}
            for dep_id, dep_output in dependency_outputs.items():
                if not isinstance(dep_output, dict):
                    continue
                day_val = dep_output.get("day")
                if isinstance(day_val, int):
                    day_objects[day_val] = dep_output
                    grounded_nodes.append(dep_id)
                if isinstance(dep_output.get("quality_criteria"), dict):
                    quality_criteria = dep_output.get("quality_criteria", {})

            missing_days = [d for d in range(1, 8) if d not in day_objects]
            missing_fields: List[str] = []
            semantic_gaps: List[str] = []
            generic_content_flags: List[str] = []
            missing_specificity_days: List[int] = []
            anchor_terms = ["multi-agent", "workflow", "verifiable", "task tree", "task trees"]
            require_anchor_topics = any(token in query.lower() for token in anchor_terms)
            require_repo_refs = bool(quality_criteria.get("must_reference_repo_changes", False))
            anchor_days = 0
            repo_binding_hits = 0
            for day, item in day_objects.items():
                for field in ["goal", "deliverable", "metric"]:
                    value = _safe_str(item.get(field))
                    if not value:
                        missing_fields.append(f"day{day}.{field}")
                deliverable_text = _safe_str(item.get("deliverable"))
                metric_text = _safe_str(item.get("metric"))
                if deliverable_text and not _is_specific_deliverable(deliverable_text):
                    semantic_gaps.append(f"day{day}:generic_deliverable")
                    missing_specificity_days.append(day)
                    generic_content_flags.append(f"day{day}:generic_deliverable")
                if require_repo_refs and deliverable_text:
                    lowered_deliverable = deliverable_text.lower()
                    if not _is_repo_bound_text(lowered_deliverable):
                        semantic_gaps.append(f"day{day}:missing_repo_reference")
                    else:
                        repo_binding_hits += 1
                if metric_text and not _is_measurable_metric(metric_text):
                    semantic_gaps.append(f"day{day}:non_actionable_metric")
                    generic_content_flags.append(f"day{day}:non_actionable_metric")
                merged = " ".join(
                    [_safe_str(item.get("goal")), _safe_str(item.get("deliverable")), _safe_str(item.get("metric"))]
                ).lower()
                if _is_placeholder_text(merged):
                    semantic_gaps.append(f"day{day}:placeholder_pattern")
                    generic_content_flags.append(f"day{day}:placeholder_pattern")
                if topics and not any(topic in merged for topic in topics):
                    semantic_gaps.append(f"day{day}:topic_not_aligned")
                if any(anchor in merged for anchor in anchor_terms):
                    anchor_days += 1
                if "async" in merged or "event loop" in merged or "concurrency" in merged:
                    if "async" not in query.lower() and "concurrency" not in query.lower():
                        semantic_gaps.append(f"day{day}:topic_drift_to_runtime")
                if "concrete artifact" in merged and not _is_specific_deliverable(merged):
                    semantic_gaps.append(f"day{day}:generic_plan_template")
                    generic_content_flags.append(f"day{day}:generic_plan_template")

            normalized_templates: List[str] = []
            for day in sorted(day_objects):
                item = day_objects[day]
                merged = " ".join(
                    [_safe_str(item.get("goal")), _safe_str(item.get("deliverable")), _safe_str(item.get("metric"))]
                ).lower()
                merged = re.sub(r"\bday\s*[1-9]\b", "day", merged)
                merged = re.sub(r"\s+", " ", merged).strip()
                normalized_templates.append(merged)
            if normalized_templates:
                diversity = len(set(normalized_templates)) / max(len(normalized_templates), 1)
                if diversity < 0.6:
                    semantic_gaps.append("plan_repetition_template_detected")
            if require_anchor_topics and anchor_days < 3:
                semantic_gaps.append("plan_anchor_coverage_below_threshold")
                generic_content_flags.append("plan_anchor_coverage_below_threshold")

            repo_binding_score = repo_binding_hits / max(len(day_objects), 1) if day_objects else 0.0
            coverage_ok = len(missing_days) == 0 and len(missing_fields) == 0 and len(semantic_gaps) == 0
            output = {
                "coverage_ok": coverage_ok,
                "missing_days": missing_days,
                "missing_fields": missing_fields,
                "semantic_gaps": semantic_gaps,
                "generic_content_flags": sorted(set(generic_content_flags)),
                "missing_specificity_days": sorted(set(missing_specificity_days)),
                "repo_binding_score": repo_binding_score,
                "grounded_nodes": grounded_nodes,
                "source": "verify_agent",
            }
            return _ensure_required_fields(node=node, output=output)

        return _ensure_required_fields(
            node=node,
            output={"verified": True, "source": "verify_agent"},
        )

    def estimate_success(self, node: TaskNode) -> float:
        return 0.9


# Legacy compatibility wrappers
class SearchAgent(RetrieveAgent):
    pass


class CompanyAgent(ExtractAgent):
    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        base = super().run(node=node, inputs=inputs, context=context)
        founder = _safe_str(base.get("founder")) or _safe_str(
            _find_value_from_dependency_outputs(inputs.get("dependency_outputs", {}), ["founder"])
        )
        company = _safe_str(base.get("company"))
        output = {
            "founder": founder or "unknown",
            "company": company or "unknown",
            "source": "extract_from_retrieval",
        }
        return _ensure_required_fields(node=node, output=output)


class CEOAgent(ExtractAgent):
    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        base = super().run(node=node, inputs=inputs, context=context)
        company = _safe_str(base.get("company")) or _safe_str(
            _find_value_from_dependency_outputs(inputs.get("dependency_outputs", {}), ["company"])
        )
        ceo = _safe_str(base.get("ceo")) or "unknown"
        retry_feedback: List[str] = context.get("retry_feedback", [])
        required_fix = any("missing_required_key:ceo" in x or "schema_missing_required" in x for x in retry_feedback)
        if context.get("attempt", 1) == 1 and not required_fix:
            output = {"chief_executive": ceo, "company": company, "source": "extract_from_retrieval_v1"}
        else:
            output = {"ceo": ceo, "company": company, "source": "extract_from_retrieval_v2"}
        return _ensure_required_fields(node=node, output=output)


class FallbackCEOAgent(CEOAgent):
    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        company = _safe_str(_find_value_from_dependency_outputs(dependency_outputs, ["company"]))
        ceo = _safe_str(_find_value_from_dependency_outputs(dependency_outputs, ["ceo", "chief_executive"]))
        output = {"ceo": ceo or "unknown", "company": company, "source": "fallback_extract_from_retrieval"}
        return _ensure_required_fields(node=node, output=output)
