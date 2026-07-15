"""Unit tests for LLM adapters (OpenAI / Anthropic / GLM).

SDK clients are mocked to avoid live API calls. Integration tests cover real behavior.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx
import openai
import pytest
from eaos.core.config import LLMConfig
from eaos.core.errors import LLMError
from eaos.infra.llm.anthropic_adapter import AnthropicLLMClient
from eaos.infra.llm.base import LLMResponse, Message, ToolCall
from eaos.infra.llm.glm_adapter import GLMLLMClient
from eaos.infra.llm.openai_adapter import OpenAILLMClient

# ---------- OpenAI helpers ----------


def _make_openai_client(**overrides: Any) -> tuple[OpenAILLMClient, Any]:
    kwargs: dict[str, Any] = {"openai_api_key": "sk-test"}
    kwargs.update(overrides)
    config = LLMConfig(**kwargs)
    client = OpenAILLMClient(config)
    mock_sdk: Any = MagicMock()
    mock_sdk.chat = MagicMock()
    mock_sdk.chat.completions = MagicMock()
    mock_sdk.chat.completions.create = AsyncMock()
    client._client = mock_sdk
    return client, mock_sdk


def _openai_chat_response(
    content: str = "hi",
    tool_calls: list[Any] | None = None,
    usage: Any | None = None,
) -> Any:
    msg = MagicMock(content=content, tool_calls=tool_calls)
    choice = MagicMock(message=msg, finish_reason="stop")
    u = usage or MagicMock(prompt_tokens=10, completion_tokens=5)
    return MagicMock(choices=[choice], model="gpt-4o", usage=u)


def _openai_tool_call(tc_id: str, name: str, args_json: str) -> Any:
    func = MagicMock()
    func.name = name
    func.arguments = args_json
    tc = MagicMock()
    tc.id = tc_id
    tc.function = func
    return tc


class TestOpenAILLMClient:
    def test_provider_attribute(self) -> None:
        client, _ = _make_openai_client()
        assert client.provider == "openai"

    async def test_chat_returns_llmresponse(self) -> None:
        client, sdk = _make_openai_client()
        sdk.chat.completions.create.return_value = _openai_chat_response(content="hello")
        result = await client.chat([Message(role="user", content="hi")], model="gpt-4o")
        assert isinstance(result, LLMResponse)
        assert result.content == "hello"
        assert result.model == "gpt-4o"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5

    async def test_chat_maps_message_to_dict(self) -> None:
        client, sdk = _make_openai_client()
        sdk.chat.completions.create.return_value = _openai_chat_response()
        msgs = [
            Message(role="system", content="be brief"),
            Message(role="user", content="ping"),
        ]
        await client.chat(msgs, model="gpt-4o")
        call = sdk.chat.completions.create.call_args
        sent = call.kwargs["messages"]
        assert sent[0] == {"role": "system", "content": "be brief"}
        assert sent[1] == {"role": "user", "content": "ping"}

    async def test_chat_passes_tools(self) -> None:
        client, sdk = _make_openai_client()
        sdk.chat.completions.create.return_value = _openai_chat_response()
        tools = [{"type": "function", "function": {"name": "noop"}}]
        await client.chat([Message(role="user", content="x")], model="gpt-4o", tools=tools)
        call = sdk.chat.completions.create.call_args
        assert call.kwargs["tools"] == tools

    async def test_chat_parses_tool_calls(self) -> None:
        client, sdk = _make_openai_client()
        tc = _openai_tool_call("call_1", "search", '{"q": "rust"}')
        sdk.chat.completions.create.return_value = _openai_chat_response(
            content="", tool_calls=[tc]
        )
        result = await client.chat([Message(role="user", content="x")], model="gpt-4o")
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0] == ToolCall(id="call_1", name="search", arguments={"q": "rust"})

    async def test_chat_translates_api_error(self) -> None:
        client, sdk = _make_openai_client()
        sdk.chat.completions.create.side_effect = openai.OpenAIError("boom")
        with pytest.raises(LLMError):
            await client.chat([Message(role="user", content="x")], model="gpt-4o")

    async def test_stream_yields_deltas(self) -> None:
        client, sdk = _make_openai_client()
        chunks = [
            MagicMock(choices=[MagicMock(delta=MagicMock(content="a"))]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content="b"))]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content=None))]),
        ]

        async def _aiter() -> Any:
            for c in chunks:
                yield c

        sdk.chat.completions.create.return_value = _aiter()
        tokens = [
            t async for t in client.stream([Message(role="user", content="x")], model="gpt-4o")
        ]
        assert tokens == ["a", "b"]

    async def test_vision_returns_content(self) -> None:
        client, sdk = _make_openai_client()
        sdk.chat.completions.create.return_value = _openai_chat_response(content="a cat")
        result = await client.vision(b"\x89PNG", "what is this?", model="gpt-4o")
        assert result == "a cat"


# ---------- Anthropic helpers ----------


def _make_anthropic_client(**overrides: Any) -> tuple[AnthropicLLMClient, Any]:
    kwargs: dict[str, Any] = {"anthropic_api_key": "sk-ant-test"}
    kwargs.update(overrides)
    config = LLMConfig(**kwargs)
    client = AnthropicLLMClient(config)
    mock_sdk: Any = MagicMock()
    mock_sdk.messages = MagicMock()
    mock_sdk.messages.create = AsyncMock()
    mock_sdk.messages.stream = MagicMock()
    client._client = mock_sdk
    return client, mock_sdk


def _anthropic_response(
    blocks: list[Any] | None = None,
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> Any:
    return MagicMock(
        content=blocks or [MagicMock(type="text", text="hi")],
        model="claude-3-5-sonnet",
        usage=MagicMock(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason="end_turn",
    )


class TestAnthropicLLMClient:
    def test_provider_attribute(self) -> None:
        client, _ = _make_anthropic_client()
        assert client.provider == "anthropic"

    async def test_chat_extracts_system_message(self) -> None:
        client, sdk = _make_anthropic_client()
        sdk.messages.create.return_value = _anthropic_response()
        msgs = [
            Message(role="system", content="be brief"),
            Message(role="user", content="hi"),
        ]
        await client.chat(msgs, model="claude-3-5-sonnet")
        call = sdk.messages.create.call_args
        assert call.kwargs["system"] == "be brief"
        sent = call.kwargs["messages"]
        assert len(sent) == 1
        assert sent[0]["role"] == "user"

    async def test_chat_parses_text_and_tool_use(self) -> None:
        client, sdk = _make_anthropic_client()
        tool_block = MagicMock(type="tool_use", id="tu_1", input={"q": "rust"})
        tool_block.name = "search"
        blocks = [
            MagicMock(type="text", text="calling tool"),
            tool_block,
        ]
        sdk.messages.create.return_value = _anthropic_response(blocks=blocks)
        result = await client.chat([Message(role="user", content="x")], model="claude")
        assert result.content == "calling tool"
        assert result.tool_calls is not None
        assert result.tool_calls[0] == ToolCall(id="tu_1", name="search", arguments={"q": "rust"})

    async def test_chat_translates_api_error(self) -> None:
        client, sdk = _make_anthropic_client()
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        sdk.messages.create.side_effect = anthropic.APIError(
            "boom", request=req, body=None
        )
        with pytest.raises(LLMError):
            await client.chat([Message(role="user", content="x")], model="claude")

    async def test_chat_passes_max_tokens(self) -> None:
        client, sdk = _make_anthropic_client()
        sdk.messages.create.return_value = _anthropic_response()
        await client.chat([Message(role="user", content="x")], model="claude")
        call = sdk.messages.create.call_args
        assert call.kwargs["max_tokens"] == 1024


# ---------- GLM helpers ----------


class TestGLMLLMClient:
    def test_provider_attribute(self) -> None:
        config = LLMConfig(glm_api_key="glm-test")
        client = GLMLLMClient(config)
        assert client.provider == "glm"

    def test_uses_glm_base_url(self) -> None:
        config = LLMConfig(glm_api_key="glm-test")
        client = GLMLLMClient(config)
        assert client._inner._base_url_override == "https://open.bigmodel.cn/api/paas/v4"

    def test_uses_glm_api_key(self) -> None:
        config = LLMConfig(glm_api_key="glm-test")
        client = GLMLLMClient(config)
        assert client._inner._api_key_override == "glm-test"

    async def test_chat_delegates_to_inner(self) -> None:
        config = LLMConfig(glm_api_key="glm-test")
        client = GLMLLMClient(config)
        mock_sdk: Any = MagicMock()
        mock_sdk.chat = MagicMock()
        mock_sdk.chat.completions = MagicMock()
        mock_sdk.chat.completions.create = AsyncMock()
        mock_sdk.chat.completions.create.return_value = _openai_chat_response(content="glm reply")
        client._inner._client = mock_sdk
        result = await client.chat([Message(role="user", content="hi")], model="glm-4")
        assert result.content == "glm reply"
