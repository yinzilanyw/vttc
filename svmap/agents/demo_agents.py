from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from svmap.models import TaskNode

from .base import BaseAgent


def _normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


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


def _flatten_dependency_text(dependency_outputs: Dict[str, Dict[str, Any]]) -> str:
    chunks: List[str] = []
    for dep_id, dep_output in dependency_outputs.items():
        if isinstance(dep_output, dict):
            chunks.append(f"{dep_id}={dep_output}")
    return " | ".join(chunks)


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


class RetrieveAgent(BaseAgent):
    def __init__(self, knowledge_base: Optional[Dict[str, Dict[str, str]]] = None) -> None:
        self.knowledge_base = knowledge_base or {}

    def supported_task_types(self) -> List[str]:
        return ["tool_call", "retrieval", "reasoning", "extraction", "summarization", "comparison"]

    def supported_output_modes(self) -> List[str]:
        return ["text", "json", "table"]

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        query = _safe_str(inputs.get("node_inputs", {}).get("query"))
        if not query:
            query = _safe_str(inputs.get("global_context", {}).get("query"))

        founder = "unknown"
        match = re.search(r"founded by\s+([A-Za-z .'-]+)\??", query, re.IGNORECASE)
        if match:
            founder = match.group(1).strip()
        elif inputs.get("node_inputs", {}).get("founder_hint"):
            founder = _safe_str(inputs["node_inputs"]["founder_hint"])

        facts = self.knowledge_base.get(_normalize_name(founder), {})
        output = {
            "query": query,
            "evidence": f"query={query}; founder={founder}; facts={facts}",
            "source": "knowledge_base" if facts else "query_text",
        }
        if founder != "unknown":
            output["founder"] = founder
        if facts.get("company"):
            output["company"] = facts["company"]
        if facts.get("ceo"):
            output["ceo"] = facts["ceo"]
        return _ensure_required_fields(node=node, output=output)

    def estimate_success(self, node: TaskNode) -> float:
        return 0.92


class ExtractAgent(BaseAgent):
    def __init__(self, knowledge_base: Optional[Dict[str, Dict[str, str]]] = None) -> None:
        self.knowledge_base = knowledge_base or {}

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

        founder = _find_value_from_dependency_outputs(dependency_outputs, ["founder", "person", "subject"])
        if founder is None:
            m = re.search(r"founded by\s+([A-Za-z .'-]+)\??", combined, re.IGNORECASE)
            founder = m.group(1).strip() if m else None
        founder = _safe_str(founder)

        company = _find_value_from_dependency_outputs(dependency_outputs, ["company", "organization"])
        ceo = _find_value_from_dependency_outputs(dependency_outputs, ["ceo", "answer"])
        if founder:
            facts = self.knowledge_base.get(_normalize_name(founder), {})
            company = company or facts.get("company")
            ceo = ceo or facts.get("ceo")

        extracted: Dict[str, Any] = {}
        if founder:
            extracted["founder"] = founder
        if company:
            extracted["company"] = company
        if ceo:
            extracted["ceo"] = ceo

        output = {
            "extracted": extracted,
            "source": "pattern_extractor",
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
            "summary": summary[:400],
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
    def __init__(self, knowledge_base: Optional[Dict[str, Dict[str, str]]] = None) -> None:
        self.knowledge_base = knowledge_base or {}

    def supported_task_types(self) -> List[str]:
        return ["final_response", "synthesis", "aggregation", "reasoning"]

    def supported_output_modes(self) -> List[str]:
        return ["text", "json", "table"]

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        query = _safe_str(inputs.get("global_context", {}).get("query"))

        answer = ""
        for dep_output in dependency_outputs.values():
            if not isinstance(dep_output, dict):
                continue
            for key in ("answer", "summary", "comparison", "result", "ceo", "company"):
                value = dep_output.get(key)
                if isinstance(value, str) and value.strip():
                    answer = value.strip()
                    break
                if isinstance(value, (int, float)):
                    answer = str(value)
                    break
            if answer:
                break

        if not answer and query:
            founder_match = re.search(r"founded by\s+([A-Za-z .'-]+)\??", query, re.IGNORECASE)
            if founder_match:
                founder = _normalize_name(founder_match.group(1))
                facts = self.knowledge_base.get(founder, {})
                if facts.get("ceo"):
                    answer = facts["ceo"]

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
        if founder and not company:
            company = self.knowledge_base.get(_normalize_name(founder), {}).get("company", "")
        output = {
            "founder": founder or "unknown",
            "company": company or "unknown",
            "source": "kb_lookup",
        }
        return _ensure_required_fields(node=node, output=output)


class CEOAgent(ExtractAgent):
    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        base = super().run(node=node, inputs=inputs, context=context)
        founder = _safe_str(base.get("founder"))
        company = _safe_str(base.get("company")) or _safe_str(
            _find_value_from_dependency_outputs(inputs.get("dependency_outputs", {}), ["company"])
        )
        ceo = _safe_str(base.get("ceo"))
        if founder and not ceo:
            ceo = self.knowledge_base.get(_normalize_name(founder), {}).get("ceo", "")
        retry_feedback: List[str] = context.get("retry_feedback", [])
        required_fix = any("missing_required_key:ceo" in x or "schema_missing_required" in x for x in retry_feedback)
        if context.get("attempt", 1) == 1 and not required_fix:
            output = {"chief_executive": ceo or "unknown", "company": company, "source": "kb_lookup_v1"}
        else:
            output = {"ceo": ceo or "unknown", "company": company, "source": "kb_lookup_v2"}
        return _ensure_required_fields(node=node, output=output)


class FallbackCEOAgent(CEOAgent):
    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        founder = _safe_str(_find_value_from_dependency_outputs(dependency_outputs, ["founder"])) or "unknown"
        company = _safe_str(_find_value_from_dependency_outputs(dependency_outputs, ["company"]))
        ceo = self.knowledge_base.get(_normalize_name(founder), {}).get("ceo", "unknown")
        output = {"ceo": ceo, "company": company, "source": "fallback_kb_lookup"}
        return _ensure_required_fields(node=node, output=output)
