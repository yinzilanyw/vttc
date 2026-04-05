from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from svmap.models import IntentSpec, TaskNode


def _load_openai_client(api_key: str, base_url: str) -> Any:
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=base_url)


class LLMCapableMixin:
    """为需要 LLM 调用的 Agent 提供统一接口。"""

    client: Any = None
    model: str = ""

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        expect_json: bool = False,
    ) -> str:
        """调用 LLM，返回文本内容。失败时抛出 RuntimeError。"""
        if self.client is None:
            raise RuntimeError("LLM client not initialized.")
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        return content.strip()

    def _call_llm_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """调用 LLM 并解析 JSON 返回。失败时返回空 dict。"""
        try:
            text = self._call_llm(system_prompt, user_prompt, expect_json=True)
            # 去除可能的 markdown 代码块
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            return json.loads(text)
        except Exception:
            return {}


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
