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
    confidence: Optional[float] = None


class VerifierEngine:
    def __init__(self, verifiers: List[BaseVerifier]) -> None:
        self.verifiers = verifiers

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
        scope: str = "node",
    ) -> VerificationResult:
        details: List[ConstraintResult] = []
        task_type = node.spec.task_type
        for verifier in self.verifiers:
            if scope not in verifier.supports_scope():
                continue
            supported_task_types = verifier.supports_task_types()
            if "*" not in supported_task_types and task_type not in supported_task_types:
                continue
            details.extend(verifier.verify(node=node, output=output, context=context))

        errors = [item for item in details if not item.passed and item.severity == "error"]
        reasons = [f"{item.code}:{item.message}" for item in errors]
        return VerificationResult(
            passed=len(errors) == 0,
            reasons=reasons,
            details=details,
            confidence=1.0 if len(errors) == 0 else 0.0,
        )

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
