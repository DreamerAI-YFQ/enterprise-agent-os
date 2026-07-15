"""GLM (Zhipu AI) LLM adapter — thin wrapper over OpenAILLMClient.

GLM exposes an OpenAI-compatible API at https://open.bigmodel.cn/api/paas/v4.
We reuse the OpenAI adapter with a fixed base_url and the glm_api_key, avoiding
a separate zhipuai SDK dependency. Tool-calling format may differ slightly;
calibration deferred to Phase 3 once real GLM tool-use traces are available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eaos.infra.llm.openai_adapter import OpenAILLMClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.core.config import LLMConfig
    from eaos.infra.llm.base import LLMResponse, Message


class GLMLLMClient:
    """LLM adapter for GLM via its OpenAI-compatible endpoint."""

    provider: str = "glm"

    _GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

    def __init__(self, config: LLMConfig) -> None:
        self._inner = OpenAILLMClient(
            config,
            api_key=config.glm_api_key,
            base_url=self._GLM_BASE_URL,
        )

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: object,
    ) -> LLMResponse:
        return await self._inner.chat(
            messages,
            model=model,
            temperature=temperature,
            tools=tools,
            **kwargs,
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.7,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        async for token in self._inner.stream(
            messages, model=model, temperature=temperature, **kwargs
        ):
            yield token

    async def vision(self, image: bytes, prompt: str, *, model: str) -> str:
        return await self._inner.vision(image, prompt, model=model)
