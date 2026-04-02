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
    ) -> VerificationResult:
        details: List[ConstraintResult] = []
        for verifier in self.verifiers:
            details.extend(verifier.verify(node=node, output=output, context=context))

        errors = [item for item in details if not item.passed and item.severity == "error"]
        reasons = [f"{item.code}:{item.message}" for item in errors]
        return VerificationResult(
            passed=len(errors) == 0,
            reasons=reasons,
            details=details,
            confidence=1.0 if len(errors) == 0 else 0.0,
        )
