from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from svmap.models import TaskNode

from .base import BaseAgent


def _normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _find_value_from_dependency_outputs(
    dependency_outputs: Dict[str, Dict[str, Any]],
    keys: List[str],
) -> Optional[Any]:
    for dep_output in dependency_outputs.values():
        for key in keys:
            value = dep_output.get(key)
            if value is not None and (not isinstance(value, str) or value.strip()):
                return value
    return None


class SearchAgent(BaseAgent):
    def __init__(self, knowledge_base: Optional[Dict[str, Dict[str, str]]] = None) -> None:
        self.knowledge_base = knowledge_base or {}

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        query = inputs["node_inputs"].get("query", "")
        if not query:
            query = inputs.get("global_context", {}).get("query", "")
        founder_hint = inputs["node_inputs"].get("founder_hint", "")
        if founder_hint:
            founder = founder_hint
        else:
            match = re.search(r"founded by\s+([A-Za-z .'-]+)\??", query, re.IGNORECASE)
            founder = match.group(1).strip() if match else "unknown"

        facts = self.knowledge_base.get(_normalize_name(founder), {})
        company = facts.get("company")
        ceo = facts.get("ceo")
        evidence = f"founder={founder}; company={company or 'unknown'}; ceo={ceo or 'unknown'}"
        return {
            "founder": founder,
            "company": company,
            "company_name": company,
            "ceo": ceo,
            "ceo_name": ceo,
            "evidence": evidence,
            "source": "query_parser",
        }

    def estimate_success(self, node: TaskNode) -> float:
        return 0.9 if node.spec.capability_tag == "search" else 0.6


class CompanyAgent(BaseAgent):
    def __init__(self, knowledge_base: Dict[str, Dict[str, str]]) -> None:
        self.knowledge_base = knowledge_base

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        founder = _find_value_from_dependency_outputs(dependency_outputs, ["founder", "founder_name"])
        if founder is None:
            founder = inputs["node_inputs"].get("founder_hint", "unknown")
        facts = self.knowledge_base.get(_normalize_name(founder), {})
        return {
            "founder": founder,
            "company": facts.get("company"),
            "source": "kb_lookup",
        }

    def estimate_success(self, node: TaskNode) -> float:
        return 0.9 if node.spec.capability_tag in {"lookup", "search"} else 0.5


class CEOAgent(BaseAgent):
    def __init__(self, knowledge_base: Dict[str, Dict[str, str]]) -> None:
        self.knowledge_base = knowledge_base

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        company = _find_value_from_dependency_outputs(
            dependency_outputs, ["company", "company_name", "company_id"]
        )
        founder = _find_value_from_dependency_outputs(
            dependency_outputs, ["founder", "founder_name"]
        ) or "unknown"
        facts = self.knowledge_base.get(_normalize_name(founder), {})
        ceo = facts.get("ceo")

        retry_feedback: List[str] = context.get("retry_feedback", [])
        required_fix = any("missing_required_key:ceo" in x for x in retry_feedback)
        if context["attempt"] == 1 and not required_fix:
            return {"chief_executive": ceo, "company": company, "source": "kb_lookup_v1"}
        return {"ceo": ceo, "company": company, "source": "kb_lookup_v2"}

    def estimate_success(self, node: TaskNode) -> float:
        return 0.8


class FallbackCEOAgent(BaseAgent):
    def __init__(self, knowledge_base: Dict[str, Dict[str, str]]) -> None:
        self.knowledge_base = knowledge_base

    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        dependency_outputs = inputs.get("dependency_outputs", {})
        founder = _find_value_from_dependency_outputs(
            dependency_outputs, ["founder", "founder_name"]
        ) or "unknown"
        company = _find_value_from_dependency_outputs(
            dependency_outputs, ["company", "company_name", "company_id"]
        )
        facts = self.knowledge_base.get(_normalize_name(founder), {})
        return {"ceo": facts.get("ceo"), "company": company, "source": "fallback_kb_lookup"}

    def estimate_success(self, node: TaskNode) -> float:
        return 0.95
