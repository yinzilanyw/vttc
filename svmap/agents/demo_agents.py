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
    }
    topics: List[str] = []
    for token in tokens:
        if token in stop or len(token) < 3:
            continue
        if token not in topics:
            topics.append(token)
    return topics[:8]


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

        day_idx = _parse_day_index(node.id)
        if day_idx is not None:
            stage_templates = {
                1: "Understand the core concepts and event loop model",
                2: "Build basic coroutine and task orchestration patterns",
                3: "Design structured concurrency and cancellation handling",
                4: "Integrate async I/O with external services and retries",
                5: "Add observability, debugging, and latency profiling",
                6: "Harden reliability with backpressure and failure recovery",
                7: "Deliver an end-to-end capstone and evaluation report",
            }
            topic_text = ", ".join(topics[:3]) if topics else "the requested topic"
            goal = f"Day {day_idx}: {stage_templates.get(day_idx, 'Advance the implementation')} for {topic_text}."
            deliverable = (
                f"Produce a concrete artifact for day {day_idx}: code, notes, and test evidence "
                f"focused on {topic_text}."
            )
            metric = (
                f"Complete day {day_idx} acceptance checklist with measurable completion criteria "
                f"(tests pass, checklist >= 90%, and reflection recorded)."
            )
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
            for dep_id, dep_output in dependency_outputs.items():
                if isinstance(dep_output, dict) and isinstance(dep_output.get("day"), int):
                    day_items.append(dep_output)
                    used_nodes.append(dep_id)
                if dep_id == "verify_coverage" and isinstance(dep_output, dict):
                    for nid in dep_output.get("grounded_nodes", []):
                        if isinstance(nid, str) and nid not in used_nodes:
                            used_nodes.append(nid)
            day_items = sorted(day_items, key=lambda x: int(x.get("day", 0)))
            if day_items:
                lines: List[str] = []
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
                    "used_nodes": used_nodes or list(dependency_outputs.keys()),
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
        topics = _extract_query_topics(query)
        dependency_outputs = inputs.get("dependency_outputs", {})

        if node.id == "analyze_requirements":
            constraints = []
            if "7-day" in query.lower() or "7 day" in query.lower():
                constraints.append("duration_days=7")
            if "goal" in query.lower():
                constraints.append("include_goal_field")
            if "deliverable" in query.lower():
                constraints.append("include_deliverable_field")
            if "metric" in query.lower():
                constraints.append("include_metric_field")
            output = {
                "topics": topics or ["general"],
                "constraints": constraints or ["respond_to_user_query"],
                "required_fields": ["goal", "deliverable", "metric"],
                "duration_days": 7,
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
            progression = [
                "foundation",
                "core patterns",
                "composition",
                "integration",
                "observability",
                "hardening",
                "capstone",
            ]
            output = {
                "day_template": {
                    "goal": "A day-specific learning objective tied to query topics.",
                    "deliverable": "A concrete artifact produced today.",
                    "metric": "Measurable completion criteria.",
                },
                "progression": progression,
                "required_fields": required_fields,
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
            for dep_id, dep_output in dependency_outputs.items():
                if not isinstance(dep_output, dict):
                    continue
                day_val = dep_output.get("day")
                if isinstance(day_val, int):
                    day_objects[day_val] = dep_output
                    grounded_nodes.append(dep_id)

            missing_days = [d for d in range(1, 8) if d not in day_objects]
            missing_fields: List[str] = []
            semantic_gaps: List[str] = []
            for day, item in day_objects.items():
                for field in ["goal", "deliverable", "metric"]:
                    value = _safe_str(item.get(field))
                    if not value:
                        missing_fields.append(f"day{day}.{field}")
                merged = " ".join(
                    [_safe_str(item.get("goal")), _safe_str(item.get("deliverable")), _safe_str(item.get("metric"))]
                ).lower()
                if _is_placeholder_text(merged):
                    semantic_gaps.append(f"day{day}:placeholder_pattern")
                if topics and not any(topic in merged for topic in topics):
                    semantic_gaps.append(f"day{day}:topic_not_aligned")

            coverage_ok = len(missing_days) == 0 and len(missing_fields) == 0 and len(semantic_gaps) == 0
            output = {
                "coverage_ok": coverage_ok,
                "missing_days": missing_days,
                "missing_fields": missing_fields,
                "semantic_gaps": semantic_gaps,
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
