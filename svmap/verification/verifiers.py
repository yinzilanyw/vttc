from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from svmap.models import ConstraintResult, TaskNode
from svmap.models.constraints import ConsistencyConstraint, RequiredFieldsConstraint

from .base import BaseVerifier


class SchemaVerifier(BaseVerifier):
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
                    evidence={"missing_fields": missing},
                )
            )
        return results


class RuleVerifier(BaseVerifier):
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
        if missing:
            node.mark_intent_violated(
                f"intent outputs missing fields: {missing}"
            )
            return [
                ConstraintResult(
                    passed=False,
                    code="intent_mismatch",
                    message=f"Intent semantics not satisfied, missing fields: {missing}",
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
                    violation_scope="edge",
                    repair_hint="apply_normalization_patch",
                )
            ]
        return []
