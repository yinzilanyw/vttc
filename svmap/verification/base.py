from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from svmap.models import ConstraintResult, TaskNode


class BaseVerifier(ABC):
    def supports_scope(self) -> List[str]:
        return ["node"]

    def supports_constraint_types(self) -> List[str]:
        return ["*"]

    def supports_task_types(self) -> List[str]:
        return ["*"]

    @abstractmethod
    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        raise NotImplementedError
