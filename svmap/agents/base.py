from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

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
