"""Anthropic LLM adapter implementing LLMClient protocol.

Uses anthropic.AsyncAnthropic. Anthropic's API differs from OpenAI: system
messages are passed as a top-level param (not in messages), tool results are
content blocks (not tool-role messages), and responses are lists of content
blocks (text/tool_use). This adapter normalizes those into EAOS Message/ToolCall.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

import anthropic
from eaos.core.errors import LLMError
from eaos.infra.llm.base import LLMResponse, Message, ToolCall

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.core.config import LLMConfig


class AnthropicLLMClient:
    """LLM adapter backed by anthropic.AsyncAnthropic."""

    provider: str = "anthropic"

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            kwargs: dict[str, Any] = {"timeout": self._config.request_timeout_sec}
            if self._config.anthropic_api_key is not None:
                kwargs["api_key"] = self._config.anthropic_api_key
            self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    @staticmethod
    def _split_messages(
        messages: list[Message],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Split EAOS messages into Anthropic (system, messages) pair."""
        system: str | None = None
        anthropic_msgs: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system = m.content
            elif m.role == "tool":
                anthropic_msgs.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "",
                                "content": m.content,
                            }
                        ],
                    }
                )
            else:
                anthropic_msgs.append({"role": m.role, "content": m.content})
        return system, anthropic_msgs

    @staticmethod
    def _parse_content_blocks(blocks: list[Any]) -> tuple[str, list[ToolCall]]:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in blocks:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                raw_input = getattr(block, "input", {}) or {}
                arguments: dict[str, Any] = dict(raw_input) if raw_input else {}
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        arguments=arguments,
                    )
                )
        return "".join(text_parts), tool_calls

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: object,
    ) -> LLMResponse:
        client = self._get_client()
        system, anthropic_msgs = self._split_messages(messages)
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_msgs,
            "temperature": temperature,
            "max_tokens": 1024,
        }
        if system is not None:
            create_kwargs["system"] = system
        if tools is not None:
            create_kwargs["tools"] = tools
        _merge_extra_kwargs(create_kwargs, kwargs)
        try:
            response = await client.messages.create(**create_kwargs)
        except anthropic.APIError as exc:
            raise LLMError(f"anthropic chat failed: {exc}") from exc
        content, tool_calls = self._parse_content_blocks(response.content)
        usage = response.usage
        return LLMResponse(
            content=content,
            tool_calls=tool_calls if tool_calls else None,
            prompt_tokens=usage.input_tokens if usage else 0,
            completion_tokens=usage.output_tokens if usage else 0,
            model=response.model,
            finish_reason=response.stop_reason or "stop",
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.7,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        client = self._get_client()
        system, anthropic_msgs = self._split_messages(messages)
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_msgs,
            "temperature": temperature,
            "max_tokens": 1024,
        }
        if system is not None:
            create_kwargs["system"] = system
        _merge_extra_kwargs(create_kwargs, kwargs)
        try:
            async with client.messages.stream(**create_kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.APIError as exc:
            raise LLMError(f"anthropic stream failed: {exc}") from exc

    async def vision(self, image: bytes, prompt: str, *, model: str) -> str:
        client = self._get_client()
        b64 = base64.b64encode(image).decode()
        messages: list[Any] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        try:
            response = await client.messages.create(
                model=model,
                messages=messages,
                max_tokens=1024,
            )
        except anthropic.APIError as exc:
            raise LLMError(f"anthropic vision failed: {exc}") from exc
        content, _ = self._parse_content_blocks(response.content)
        return content


def _merge_extra_kwargs(target: dict[str, Any], extra: object) -> None:
    """Merge non-None keyword args from `extra` (a dict) into `target`."""
    if isinstance(extra, dict):
        for k, v in extra.items():
            if v is not None:
                target[k] = v
