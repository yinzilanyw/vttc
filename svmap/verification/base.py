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

    def verify_edge(
        self,
        src_node: TaskNode,
        dst_node: TaskNode,
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        return []

    def verify_subtree(
        self,
        tree: Any,
        root_node_id: str,
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        return []

    def verify_global(
        self,
        tree: Any,
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        return []

    @abstractmethod
    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        raise NotImplementedError
