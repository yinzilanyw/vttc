from __future__ import annotations

from dataclasses import dataclass
import difflib
import re
from typing import Any, Callable, Dict, List, Optional

from svmap.models import ConstraintResult, TaskNode
from svmap.models.constraints import ConsistencyConstraint, RequiredFieldsConstraint

from .base import BaseVerifier


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _normalize_text(text: str) -> str:
    lowered = _as_text(text).lower()
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def _similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, _normalize_text(left), _normalize_text(right)).ratio()


def _is_plan_query(query: str) -> bool:
    text = _normalize_text(query)
    keywords = ["plan", "learning plan", "7-day", "daily goals", "deliverables", "metric"]
    return any(k in text for k in keywords)


def _detect_day_count(text: str) -> int:
    if not text:
        return 0
    hits = re.findall(r"\bday\s*([1-9]|10)\b", _normalize_text(text))
    return len(set(hits))


def _contains_plan_sections(text: str) -> bool:
    lowered = _normalize_text(text)
    return all(token in lowered for token in ["goal", "deliverable", "metric"])


class SchemaVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["*"]

    def supports_constraint_types(self) -> List[str]:
        return ["schema", "required_fields", "field_type"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []
        if not isinstance(output, dict):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_error",
                    message="Output must be dict.",
                    failure_type="schema",
                )
            )
            return results

        required_fields = node.spec.io.required_output_field_names()
        missing = [f for f in required_fields if f not in output]
        if missing:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_missing_required",
                    message=f"Missing output schema fields: {missing}",
                    failure_type="schema",
                    evidence={"missing_fields": missing},
                )
            )
        return results


class RuleVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["*"]

    def supports_constraint_types(self) -> List[str]:
        return [
            "required_fields",
            "non_empty",
            "field_type",
            "factuality",
            "consistency",
            "final_structure",
            "non_empty_extraction",
            "no_internal_error",
            "non_trivial_transform",
        ]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []
        for constraint in node.spec.constraints:
            result = constraint.validate(node=node, output=output, context=context)
            if not result.passed:
                results.append(result)
        return results


@dataclass
class SemanticVerdict:
    passed: bool
    reason: str = ""
    confidence: float = 0.5
    repair_hint: str = ""


class SemanticVerifier(BaseVerifier):
    def __init__(
        self,
        semantic_judge: Optional[
            Callable[[Dict[str, Any], List[str], Dict[str, Any]], SemanticVerdict | bool]
        ] = None,
    ) -> None:
        self.semantic_judge = semantic_judge

    def supports_constraint_types(self) -> List[str]:
        return ["semantic", "factuality", "intent_alignment"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        raw_constraints: List[str] = []
        for c in node.spec.constraints:
            if isinstance(c, RequiredFieldsConstraint):
                raw_constraints.append(f"required_keys:{','.join(c.fields)}")
            else:
                raw_constraints.append(c.constraint_type)

        if self.semantic_judge is None:
            if "factuality" in raw_constraints:
                dep_outputs = context.get("dependency_outputs", {})
                has_evidence = any("evidence" in (item or {}) for item in dep_outputs.values())
                if not has_evidence:
                    return [
                        ConstraintResult(
                            passed=False,
                            code="semantic_check_failed",
                            message="Factual node lacks upstream evidence.",
                            failure_type="evidence",
                        )
                    ]
            return []
        verdict = self.semantic_judge(output, raw_constraints, context)
        if isinstance(verdict, bool):
            verdict = SemanticVerdict(passed=verdict)
        elif isinstance(verdict, dict):
            verdict = SemanticVerdict(
                passed=bool(verdict.get("passed", False)),
                reason=str(verdict.get("reason", "")),
                confidence=float(verdict.get("confidence", 0.5)),
                repair_hint=str(verdict.get("repair_hint", "")),
            )
        if verdict.passed:
            return []
        return [
            ConstraintResult(
                passed=False,
                code="semantic_check_failed",
                message=verdict.reason or "Semantic verifier judged the node output as insufficient.",
                failure_type="semantic",
                confidence=verdict.confidence,
                repair_hint=verdict.repair_hint,
            )
        ]


class CustomNodeVerifier(BaseVerifier):
    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        custom = node.metadata.get("custom_verifier")
        if custom is None:
            return []
        result = custom(node, output, context)
        if isinstance(result, ConstraintResult):
            return [] if result.passed else [result]
        if isinstance(result, bool):
            if result:
                return []
            return [
                ConstraintResult(
                    passed=False,
                    code="custom_verifier_failed",
                    message="Custom verifier returned False.",
                    failure_type="rule",
                )
            ]
        return []


class CrossNodeVerifier(BaseVerifier):
    def supports_scope(self) -> List[str]:
        return ["node", "edge"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []
        for constraint in node.spec.constraints:
            if isinstance(constraint, ConsistencyConstraint):
                result = constraint.validate(node=node, output=output, context=context)
                if not result.passed:
                    results.append(result)
        return results


class IntentVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["*"]

    def supports_constraint_types(self) -> List[str]:
        return ["intent_alignment"]

    def _intent_family(self, node: TaskNode) -> str:
        task_type = _normalize_text(node.spec.task_type)
        goal = _normalize_text(node.spec.intent.goal if node.spec.intent else "")
        merged = f"{task_type} {goal}"
        if "plan" in merged:
            return "plan"
        if "summary" in merged or "summar" in merged:
            return "summary"
        if "compare" in merged:
            return "compare"
        if "calculate" in merged:
            return "calculate"
        if "extract" in merged:
            return "extract"
        return ""

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        intent = node.spec.intent
        if intent is None:
            return []

        missing: List[str] = []
        for field_name in intent.output_semantics.keys():
            if field_name not in output:
                missing.append(field_name)
        dependency_outputs = context.get("dependency_outputs", {})
        missing_upstream_intents: List[str] = []
        for required_goal in intent.required_upstream_intents:
            found = False
            for dep_id in node.dependencies:
                dep_output = dependency_outputs.get(dep_id, {})
                if isinstance(dep_output, dict) and dep_output:
                    found = True
                    break
            if not found:
                missing_upstream_intents.append(required_goal)
        if missing_upstream_intents:
            node.mark_intent_violated(f"missing upstream intents: {missing_upstream_intents}")
            return [
                ConstraintResult(
                    passed=False,
                    code="intent_upstream_missing",
                    message=f"Missing required upstream intents: {missing_upstream_intents}",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            ]
        if missing:
            node.mark_intent_violated(f"intent outputs missing fields: {missing}")
            return [
                ConstraintResult(
                    passed=False,
                    code="intent_mismatch",
                    message=f"Intent semantics not satisfied, missing fields: {missing}",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            ]

        family = self._intent_family(node)
        query = _as_text(context.get("global_context", {}).get("query"))
        answer_text = _as_text(output.get("answer") or output.get("final_response") or output.get("summary"))
        if family == "plan":
            enforce_plan_structure = node.is_final_response()
            if not enforce_plan_structure:
                node.mark_intent_aligned()
                return []
            if _similarity(answer_text, query) >= 0.9 and len(answer_text) <= len(query) + 16:
                node.mark_intent_violated("plan output echoes query")
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_plan_query_echo",
                        message="Plan-like task output echoes the original query.",
                        failure_type="intent_misalignment",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
            if _detect_day_count(answer_text) < 3 and not isinstance(output.get("days"), list):
                node.mark_intent_violated("plan output missing day structure")
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_plan_structure_missing",
                        message="Plan-like task output lacks day-by-day structure.",
                        failure_type="intent_misalignment",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
        elif family == "summary":
            summary = _as_text(output.get("summary") or output.get("answer"))
            if len(summary) < 16:
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_summary_too_short",
                        message="Summary-like task output is too short.",
                        failure_type="intent_misalignment",
                        repair_hint="patch_subgraph",
                        violation_scope="node",
                    )
                ]
        elif family == "compare":
            compared = output.get("compared_items")
            text = _as_text(output.get("comparison") or output.get("answer"))
            if (not isinstance(compared, list) or len(compared) < 2) and not text:
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_compare_missing",
                        message="Compare-like task lacks comparable outputs.",
                        failure_type="intent_misalignment",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
        elif family == "calculate":
            if output.get("calculation_error"):
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_calculation_error",
                        message="Calculation task contains internal error.",
                        failure_type="intent_misalignment",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
            if not isinstance(output.get("result"), (int, float)):
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_calculation_missing_result",
                        message="Calculation task missing numeric result.",
                        failure_type="intent_misalignment",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
        elif family == "extract":
            extracted = output.get("extracted")
            if not isinstance(extracted, dict) or not any(v not in (None, "", [], {}) for v in extracted.values()):
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_extract_empty",
                        message="Extract task produced empty extracted content.",
                        failure_type="intent_misalignment",
                        repair_hint="patch_subgraph",
                        violation_scope="node",
                    )
                ]

        node.mark_intent_aligned()
        return []


class CrossNodeGraphVerifier(BaseVerifier):
    def supports_scope(self) -> List[str]:
        return ["node", "edge", "subtree"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        dep_outputs = context.get("dependency_outputs", {})
        if not dep_outputs:
            return []

        upstream_company = None
        for dep_output in dep_outputs.values():
            if isinstance(dep_output, dict) and dep_output.get("company"):
                upstream_company = dep_output.get("company")
                break
        if upstream_company and "company" in output and not output.get("company"):
            return [
                ConstraintResult(
                    passed=False,
                    code="cross_node_graph_inconsistency",
                    message="Downstream company is empty while upstream company exists.",
                    failure_type="consistency",
                    violation_scope="edge",
                    repair_hint="apply_normalization_patch",
                )
            ]
        return []


class RetrievalVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["tool_call", "retrieval"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        query = _as_text(
            output.get("query")
            or context.get("node_inputs", {}).get("query")
            or context.get("global_context", {}).get("query")
        )
        evidence = _as_text(output.get("evidence"))
        source = _as_text(output.get("source")).lower()
        if not query or not evidence:
            return []

        novel_fields: List[str] = []
        for key, value in output.items():
            if key in {"query", "evidence", "source"}:
                continue
            if isinstance(value, str) and value.strip():
                novel_fields.append(key)
            elif value not in (None, "", [], {}):
                novel_fields.append(key)

        sim = _similarity(query, evidence)
        is_echo = sim >= 0.92 and len(evidence) <= len(query) + 12
        if not is_echo and source == "bailian_direct" and len(evidence) <= max(24, len(query) + 4):
            is_echo = True
        if is_echo and not novel_fields:
            return [
                ConstraintResult(
                    passed=False,
                    code="echo_retrieval",
                    message="Retrieval evidence is near-identical to query.",
                    failure_type="echo_retrieval",
                    repair_hint="insert_evidence_patch",
                    violation_scope="node",
                    evidence={"similarity": sim},
                )
            ]
        return []


class ExtractionVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["extraction"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        extracted = output.get("extracted")
        if isinstance(extracted, dict):
            non_empty = [k for k, v in extracted.items() if v not in (None, "", [], {})]
            if len(non_empty) == 0:
                return [
                    ConstraintResult(
                        passed=False,
                        code="empty_extraction",
                        message="Extraction output is empty.",
                        failure_type="empty_extraction",
                        repair_hint="patch_subgraph",
                        violation_scope="node",
                    )
                ]

        if extracted is None:
            candidates = [
                value for key, value in output.items() if key not in {"source", "evidence", "query"}
            ]
            if not any(v not in (None, "", [], {}) for v in candidates):
                return [
                    ConstraintResult(
                        passed=False,
                        code="empty_extraction",
                        message="No structured extraction fields were produced.",
                        failure_type="empty_extraction",
                        repair_hint="patch_subgraph",
                        violation_scope="node",
                    )
                ]
        return []


class SummarizationVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["summarization"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        summary = output.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return [
                ConstraintResult(
                    passed=False,
                    code="summary_missing",
                    message="Summarization node must output non-empty summary.",
                    failure_type="schema",
                )
            ]

        dep_outputs = context.get("dependency_outputs", {})
        if dep_outputs and len(summary.strip()) < 8:
            return [
                ConstraintResult(
                    passed=False,
                    code="summary_too_short",
                    message="Summary is too short to cover upstream evidence.",
                    failure_type="evidence",
                    repair_hint="build_summary_patch",
                )
            ]
        return []


class ComparisonVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["comparison"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        compared_items = output.get("compared_items")
        comparison = output.get("comparison")
        if not isinstance(compared_items, list) or len(compared_items) < 2:
            return [
                ConstraintResult(
                    passed=False,
                    code="comparison_items_missing",
                    message="Comparison needs at least two compared items.",
                    failure_type="consistency",
                    repair_hint="replan_for_incomplete_comparison",
                )
            ]
        if not isinstance(comparison, str) or not comparison.strip():
            return [
                ConstraintResult(
                    passed=False,
                    code="comparison_text_missing",
                    message="Comparison node must provide comparison text.",
                    failure_type="schema",
                )
            ]
        return []


class CalculationVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["calculation"]

    def _is_valid_expression(self, expression: str) -> bool:
        text = expression.strip()
        if not text:
            return False
        if re.fullmatch(r"[0-9+\-*/(). ]+", text) is None:
            return False
        if re.search(r"[+\-*/]\s*$", text):
            return False
        return True

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        err = _as_text(output.get("calculation_error"))
        if err:
            return [
                ConstraintResult(
                    passed=False,
                    code="internal_execution_error",
                    message=f"Calculation raised internal error: {err}",
                    failure_type="internal_execution_error",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]

        expression = _as_text(output.get("expression"))
        if not self._is_valid_expression(expression):
            return [
                ConstraintResult(
                    passed=False,
                    code="calculation_expression_invalid",
                    message="Calculation expression is missing or invalid.",
                    failure_type="internal_execution_error",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]

        result = output.get("result")
        if not isinstance(result, (int, float)):
            return [
                ConstraintResult(
                    passed=False,
                    code="calculation_result_not_numeric",
                    message="Calculation result must be numeric.",
                    failure_type="internal_execution_error",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]

        trace = _as_text(output.get("calculation_trace"))
        if not trace:
            return [
                ConstraintResult(
                    passed=False,
                    code="calculation_trace_missing",
                    message="Calculation node should provide a trace.",
                    failure_type="internal_execution_error",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]
        return []


class FinalResponseVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["final_response"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        answer = _as_text(output.get("answer") or output.get("final_response"))
        if not answer:
            return [
                ConstraintResult(
                    passed=False,
                    code="final_answer_missing",
                    message="Final response node must output 'answer'.",
                    failure_type="intent_misalignment",
                    violation_scope="global",
                    repair_hint="replan_for_missing_final_response",
                )
            ]

        query = _as_text(context.get("global_context", {}).get("query"))
        if query:
            sim = _similarity(query, answer)
            if sim >= 0.9 and len(answer) <= len(query) + 16:
                return [
                    ConstraintResult(
                        passed=False,
                        code="final_answer_query_echo",
                        message="Final answer mostly repeats query and adds no useful content.",
                        failure_type="final_query_echo",
                        violation_scope="node",
                        repair_hint="replan_subtree",
                        evidence={"similarity": sim},
                    )
                ]

        if _is_plan_query(query):
            day_count = _detect_day_count(answer)
            if day_count < 7 or not _contains_plan_sections(answer):
                return [
                    ConstraintResult(
                        passed=False,
                        code="final_answer_missing_structure",
                        message="Final plan answer must contain 7-day structure with goal/deliverable/metric.",
                        failure_type="final_answer_missing_structure",
                        violation_scope="node",
                        repair_hint="build_final_response_patch",
                        evidence={"day_count": day_count},
                    )
                ]

        dependency_outputs = context.get("dependency_outputs", {})
        if dependency_outputs:
            used_nodes = output.get("used_nodes")
            if not isinstance(used_nodes, list) or not used_nodes:
                return [
                    ConstraintResult(
                        passed=False,
                        code="final_answer_not_grounded",
                        message="Final response should reference upstream nodes via used_nodes.",
                        failure_type="final_answer_not_grounded",
                        violation_scope="global",
                        repair_hint="build_final_response_patch",
                    )
                ]
            dep_ids = set(dependency_outputs.keys())
            used_ids = set(str(x) for x in used_nodes)
            coverage = len(dep_ids.intersection(used_ids)) / max(len(dep_ids), 1)
            if coverage < 0.5:
                return [
                    ConstraintResult(
                        passed=False,
                        code="final_answer_not_grounded",
                        message="Final answer references too few upstream nodes.",
                        failure_type="final_answer_not_grounded",
                        violation_scope="global",
                        repair_hint="build_final_response_patch",
                        evidence={"coverage": coverage},
                    )
                ]
        return []
