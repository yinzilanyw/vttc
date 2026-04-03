from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from svmap.models import ConstraintResult, TaskNode

from .base import BaseVerifier


@dataclass
class VerificationResult:
    passed: bool
    reasons: List[str] = field(default_factory=list)
    details: List[ConstraintResult] = field(default_factory=list)
    failure_type: str = ""
    repair_hints: List[str] = field(default_factory=list)
    violation_scopes: List[str] = field(default_factory=list)
    fatal: bool = False
    confidence: Optional[float] = None


class VerifierEngine:
    def __init__(self, verifiers: List[BaseVerifier]) -> None:
        self.verifiers = verifiers

    def select_verifiers_for_node(self, node: TaskNode, scope: str = "node") -> List[BaseVerifier]:
        selected: List[BaseVerifier] = []
        task_type = node.spec.task_type
        for verifier in self.verifiers:
            if scope not in verifier.supports_scope():
                continue
            supported_task_types = verifier.supports_task_types()
            if "*" not in supported_task_types and task_type not in supported_task_types:
                continue
            selected.append(verifier)
        return selected

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
        scope: str = "node",
    ) -> VerificationResult:
        details: List[ConstraintResult] = []
        for verifier in self.select_verifiers_for_node(node=node, scope=scope):
            details.extend(verifier.verify(node=node, output=output, context=context))

        errors = [item for item in details if not item.passed and item.severity == "error"]
        reasons = [f"{item.code}:{item.message}" for item in errors]
        failure_type = self._select_primary_failure_type(errors)
        repair_hints = sorted({x.repair_hint for x in errors if x.repair_hint})
        violation_scopes = sorted({x.violation_scope for x in errors if x.violation_scope})
        fatal_types = {
            "internal_execution_error",
            "final_answer_missing_structure",
            "final_query_echo",
            "intent_misalignment",
            "echo_retrieval",
            "empty_extraction",
            "final_answer_not_grounded",
            "final_output_not_valid",
        }
        fatal = bool(errors) and (failure_type in fatal_types)
        return VerificationResult(
            passed=len(errors) == 0,
            reasons=reasons,
            details=details,
            failure_type=failure_type,
            repair_hints=repair_hints,
            violation_scopes=violation_scopes,
            fatal=fatal,
            confidence=1.0 if len(errors) == 0 else 0.0,
        )

    def _infer_failure_type(self, item: ConstraintResult) -> str:
        if item.failure_type:
            return item.failure_type
        code = item.code.lower()
        if "internal" in code or "runtime_error" in code:
            return "internal_execution_error"
        if "final_answer_missing_structure" in code:
            return "final_answer_missing_structure"
        if "intent" in code:
            return "intent_misalignment"
        if "echo_retrieval" in code:
            return "echo_retrieval"
        if "empty_extraction" in code:
            return "empty_extraction"
        if "schema" in code or "type" in code or "required" in code:
            return "schema"
        if "consistency" in code:
            return "consistency"
        if "evidence" in code or "source" in code:
            return "evidence"
        return "rule"

    def _select_primary_failure_type(self, errors: List[ConstraintResult]) -> str:
        if not errors:
            return ""
        priority = [
            "internal_execution_error",
            "final_answer_missing_structure",
            "intent_misalignment",
            "echo_retrieval",
            "empty_extraction",
        ]
        typed: List[str] = [self._infer_failure_type(item) for item in errors]
        for target in priority:
            if target in typed:
                return target
        return typed[0]

    def verify_node(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> VerificationResult:
        return self.verify(node=node, output=output, context=context, scope="node")

    def verify_edge(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> VerificationResult:
        return self.verify(node=node, output=output, context=context, scope="edge")

    def verify_subtree(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> VerificationResult:
        return self.verify(node=node, output=output, context=context, scope="subtree")

    def verify_global(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> VerificationResult:
        return self.verify(node=node, output=output, context=context, scope="global")
