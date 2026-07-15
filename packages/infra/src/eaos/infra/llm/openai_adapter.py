"""OpenAI LLM adapter implementing LLMClient protocol.

Uses openai.AsyncOpenAI. Message <-> SDK dict conversion; tool_calls are
parsed from SDK tool_call objects with JSON-decoded arguments. openai.OpenAIError
is translated to LLMError at the adapter boundary.

Supports a configurable base_url so the same adapter backs GLM (OpenAI-compatible).
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

import openai
from eaos.core.errors import LLMError
from eaos.infra.llm.base import LLMResponse, Message, ToolCall

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.core.config import LLMConfig


class OpenAILLMClient:
    """LLM adapter backed by openai.AsyncOpenAI."""

    provider: str = "openai"

    def __init__(
        self,
        config: LLMConfig,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._config = config
        self._api_key_override = api_key
        self._base_url_override = base_url
        self._client: openai.AsyncOpenAI | None = None

    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            kwargs: dict[str, Any] = {"timeout": self._config.request_timeout_sec}
            key = (
                self._api_key_override
                if self._api_key_override is not None
                else self._config.openai_api_key
            )
            if key is not None:
                kwargs["api_key"] = key
            if self._base_url_override is not None:
                kwargs["base_url"] = self._base_url_override
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    @staticmethod
    def _message_to_dict(m: Message) -> dict[str, Any]:
        d: dict[str, Any] = {"role": m.role}
        content = _build_message_content(m)
        d["content"] = content
        if m.tool_calls is not None:
            d["tool_calls"] = m.tool_calls
        if m.tool_call_id is not None:
            d["tool_call_id"] = m.tool_call_id
        if m.name is not None:
            d["name"] = m.name
        return d

    @staticmethod
    def _parse_tool_calls(sdk_tool_calls: list[Any] | None) -> list[ToolCall] | None:
        if not sdk_tool_calls:
            return None
        result: list[ToolCall] = []
        for tc in sdk_tool_calls:
            func = getattr(tc, "function", None)
            args_str = func.arguments if func and func.arguments else "{}"
            try:
                arguments = json.loads(args_str)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            name = func.name if func else ""
            result.append(ToolCall(id=tc.id, name=name, arguments=arguments))
        return result

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
        dicts: list[Any] = [self._message_to_dict(m) for m in messages]
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": dicts,
            "temperature": temperature,
        }
        if tools is not None:
            create_kwargs["tools"] = tools
        _merge_extra_kwargs(create_kwargs, kwargs)
        try:
            response = await client.chat.completions.create(**create_kwargs)
        except openai.OpenAIError as exc:
            raise LLMError(f"openai chat failed: {exc}") from exc
        choice = response.choices[0]
        message = choice.message
        usage = response.usage
        return LLMResponse(
            content=message.content or "",
            tool_calls=self._parse_tool_calls(message.tool_calls),
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            model=response.model,
            finish_reason=choice.finish_reason or "stop",
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
        dicts: list[Any] = [self._message_to_dict(m) for m in messages]
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": dicts,
            "temperature": temperature,
            "stream": True,
        }
        _merge_extra_kwargs(create_kwargs, kwargs)
        try:
            stream = await client.chat.completions.create(**create_kwargs)
        except openai.OpenAIError as exc:
            raise LLMError(f"openai stream failed: {exc}") from exc
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta is not None:
                yield delta

    async def vision(self, image: bytes, prompt: str, *, model: str) -> str:
        client = self._get_client()
        b64 = base64.b64encode(image).decode()
        messages: list[Any] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ]
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
            )
        except openai.OpenAIError as exc:
            raise LLMError(f"openai vision failed: {exc}") from exc
        return response.choices[0].message.content or ""


def _merge_extra_kwargs(target: dict[str, Any], extra: object) -> None:
    """Merge non-None keyword args from `extra` (a dict) into `target`."""
    if isinstance(extra, dict):
        for k, v in extra.items():
            if v is not None:
                target[k] = v


def _build_message_content(m: Message) -> str | list[dict[str, Any]]:
    """Build OpenAI message content: plain string or multimodal list.

    If the message has attachments with images, content becomes a list of
    text + image_url parts. File attachments' extracted text is appended
    to the text part. Otherwise returns the plain string content.
    """
    if not m.attachments:
        return m.content

    text_parts: list[str] = [m.content] if m.content else []
    image_parts: list[dict[str, Any]] = []
    has_multimodal = False

    for att in m.attachments:
        if att.type == "image" and att.data_url:
            image_parts.append(
                {"type": "image_url", "image_url": {"url": att.data_url}}
            )
            has_multimodal = True
        elif att.type == "file" and att.text_content:
            text_parts.append(f"\n\n附件 {att.name} 内容:\n{att.text_content}")
            has_multimodal = True

    if not has_multimodal:
        return m.content

    content: list[dict[str, Any]] = []
    combined_text = "".join(text_parts).strip()
    if combined_text:
        content.append({"type": "text", "text": combined_text})
    content.extend(image_parts)
    return content
