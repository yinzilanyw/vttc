from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from svmap.models import IntentSpec, TaskNode


class BaseAgent(ABC):
    @abstractmethod
    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def supports_intent(self, intent: Optional[IntentSpec]) -> bool:
        return True

    def estimate_success(self, node: TaskNode) -> float:
        return 0.5

    def estimate_cost(self, node: TaskNode) -> float:
        return 1.0

    def supported_task_types(self) -> List[str]:
        return ["*"]

    def supported_output_modes(self) -> List[str]:
        return ["text", "json", "table", "boolean", "number"]

    def can_handle(self, capability_tag: str, output_mode: str = "text") -> bool:
        mode_ok = output_mode in self.supported_output_modes() or "*" in self.supported_output_modes()
        if not mode_ok:
            return False
        supported_task_types = self.supported_task_types()
        if "*" in supported_task_types:
            return True
        if capability_tag and capability_tag in supported_task_types:
            return True
        return True
