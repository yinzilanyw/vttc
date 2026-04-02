from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from svmap.models import ConstraintResult, TaskNode
from svmap.models.constraints import RequiredFieldsConstraint

from .base import BaseVerifier


class SchemaVerifier(BaseVerifier):
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


class SemanticVerifier(BaseVerifier):
    def __init__(
        self,
        semantic_judge: Optional[
            Callable[[Dict[str, Any], List[str], Dict[str, Any]], bool]
        ] = None,
    ) -> None:
        self.semantic_judge = semantic_judge

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
        passed = self.semantic_judge(output, raw_constraints, context)
        if passed:
            return []
        return [
            ConstraintResult(
                passed=False,
                code="semantic_check_failed",
                message="Semantic verifier judged the node output as insufficient.",
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
