from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from svmap.models import TaskNode


class BaseAgent(ABC):
    @abstractmethod
    def run(self, node: TaskNode, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError
