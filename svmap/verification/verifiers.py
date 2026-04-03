from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from svmap.models import ConstraintResult, TaskNode
from svmap.models.constraints import ConsistencyConstraint, RequiredFieldsConstraint

from .base import BaseVerifier


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
        return ["required_fields", "non_empty", "field_type", "factuality", "consistency"]

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
            # Dynamic fallback heuristic:
            # If node requires factuality but no evidence is available upstream, fail semantically.
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
                if not isinstance(dep_output, dict):
                    continue
                if dep_output:
                    found = True
                    break
            if not found:
                missing_upstream_intents.append(required_goal)
        if missing_upstream_intents:
            node.mark_intent_violated(
                f"missing upstream intents: {missing_upstream_intents}"
            )
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
            node.mark_intent_violated(
                f"intent outputs missing fields: {missing}"
            )
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

        # Lightweight graph-level sanity: if upstream has "company", downstream output should
        # not contradict with an empty/None "company" when field exists.
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

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        result = output.get("result")
        if not isinstance(result, (int, float)):
            return [
                ConstraintResult(
                    passed=False,
                    code="calculation_result_not_numeric",
                    message="Calculation result must be numeric.",
                    failure_type="schema",
                    repair_hint="build_calculation_patch",
                )
            ]
        trace = output.get("calculation_trace")
        if trace is None:
            return [
                ConstraintResult(
                    passed=False,
                    code="calculation_trace_missing",
                    message="Calculation node should provide a trace.",
                    failure_type="evidence",
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
        answer = output.get("answer") or output.get("final_response")
        if not isinstance(answer, str) or not answer.strip():
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
        dependency_outputs = context.get("dependency_outputs", {})
        if dependency_outputs and not output.get("used_nodes"):
            return [
                ConstraintResult(
                    passed=False,
                    code="final_answer_not_grounded",
                    message="Final response should reference upstream nodes via used_nodes.",
                    failure_type="evidence",
                    violation_scope="global",
                    repair_hint="build_final_response_patch",
                )
            ]
        return []
