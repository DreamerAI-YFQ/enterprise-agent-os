"""Unit tests for LLMRouterImpl routing logic."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from eaos.core.errors import LLMError
from eaos.infra.llm.base import LLMResponse, Message
from eaos.infra.llm.router import TenantModelRouting
from eaos.infra.llm.router_impl import LLMRouterImpl

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _FakeAdapter:
    """Minimal LLMClient implementation for testing routing."""

    def __init__(self, provider: str, response: str = "ok") -> None:
        self.provider = provider
        self._response = response
        self.last_model: str | None = None
        self.last_messages: list[Message] | None = None

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.7,
        tools: list[dict[str, object]] | None = None,
        **kwargs: object,
    ) -> LLMResponse:
        self.last_model = model
        self.last_messages = messages
        return LLMResponse(
            content=self._response,
            model=model,
            prompt_tokens=7,
            completion_tokens=3,
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.7,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        self.last_model = model
        yield self._response

    async def vision(self, image: bytes, prompt: str, *, model: str) -> str:
        self.last_model = model
        return self._response


def _router_with(*adapters: _FakeAdapter) -> LLMRouterImpl:
    r = LLMRouterImpl()
    for a in adapters:
        r.register_adapter(a)
    return r


class TestResolve:
    async def test_model_hint_with_provider(self) -> None:
        openai_ad = _FakeAdapter("openai", "openai-reply")
        anthropic_ad = _FakeAdapter("anthropic", "anthropic-reply")
        router = _router_with(openai_ad, anthropic_ad)
        result = await router.chat(
            [Message(role="user", content="hi")],
            model_hint="anthropic:claude-3",
        )
        assert result.content == "anthropic-reply"
        assert anthropic_ad.last_model == "claude-3"

    async def test_model_hint_bare_model_uses_default_provider(self) -> None:
        openai_ad = _FakeAdapter("openai", "openai-reply")
        router = _router_with(openai_ad)
        await router.chat(
            [Message(role="user", content="hi")],
            model_hint="gpt-4o",
        )
        assert openai_ad.last_model == "gpt-4o"

    async def test_task_type_routing(self) -> None:
        openai_ad = _FakeAdapter("openai")
        anthropic_ad = _FakeAdapter("anthropic", "claude-reply")
        router = _router_with(openai_ad, anthropic_ad)
        routing = TenantModelRouting(
            routing={"coding": "anthropic:claude-sonnet"},
            fallback="openai:gpt-4o-mini",
        )
        result = await router.chat(
            [Message(role="user", content="x")],
            task_type="coding",
            tenant_routing=routing,
        )
        assert result.content == "claude-reply"
        assert anthropic_ad.last_model == "claude-sonnet"

    async def test_fallback_when_task_type_not_in_routing(self) -> None:
        openai_ad = _FakeAdapter("openai", "openai-reply")
        router = _router_with(openai_ad)
        routing = TenantModelRouting(
            routing={"coding": "anthropic:claude"},
            fallback="openai:gpt-4o-mini",
        )
        result = await router.chat(
            [Message(role="user", content="x")],
            task_type="unknown_task",
            tenant_routing=routing,
        )
        assert result.content == "openai-reply"
        assert openai_ad.last_model == "gpt-4o-mini"

    async def test_default_when_no_hint_no_routing(self) -> None:
        openai_ad = _FakeAdapter("openai", "default-reply")
        router = _router_with(openai_ad)
        result = await router.chat([Message(role="user", content="x")])
        assert result.content == "default-reply"
        assert openai_ad.last_model == "gpt-4o-mini"

    async def test_unregistered_provider_falls_through(self) -> None:
        openai_ad = _FakeAdapter("openai", "openai-reply")
        router = _router_with(openai_ad)
        # Hint asks for unregistered provider "mistral" -> fall through to default
        result = await router.chat(
            [Message(role="user", content="x")],
            model_hint="mistral:mistral-large",
        )
        assert result.content == "openai-reply"


class TestStream:
    async def test_stream_uses_resolved_adapter(self) -> None:
        openai_ad = _FakeAdapter("openai", "tok1")
        router = _router_with(openai_ad)
        tokens = [
            t
            async for t in router.stream(
                [Message(role="user", content="x")],
                model_hint="openai:gpt-4o",
            )
        ]
        assert tokens == ["tok1"]
        assert openai_ad.last_model == "gpt-4o"


class TestVision:
    async def test_vision_prefers_anthropic(self) -> None:
        openai_ad = _FakeAdapter("openai", "openai-vision")
        anthropic_ad = _FakeAdapter("anthropic", "anthropic-vision")
        router = _router_with(openai_ad, anthropic_ad)
        result = await router.vision(b"img", "describe")
        assert result == "anthropic-vision"
        assert anthropic_ad.last_model == "claude-3-5-sonnet-20241022"

    async def test_vision_falls_to_openai(self) -> None:
        openai_ad = _FakeAdapter("openai", "openai-vision")
        router = _router_with(openai_ad)
        result = await router.vision(b"img", "describe")
        assert result == "openai-vision"
        assert openai_ad.last_model == "gpt-4o"

    async def test_vision_no_adapter_raises(self) -> None:
        router = LLMRouterImpl()
        with pytest.raises(LLMError):
            await router.vision(b"img", "describe")


class TestRegisterAdapter:
    def test_register_overwrites_same_provider(self) -> None:
        router = LLMRouterImpl()
        ad1 = _FakeAdapter("openai", "old")
        ad2 = _FakeAdapter("openai", "new")
        router.register_adapter(ad1)
        router.register_adapter(ad2)
        assert router._adapters["openai"] is ad2


class TestNoAdapters:
    async def test_chat_with_no_adapters_raises(self) -> None:
        router = LLMRouterImpl()
        with pytest.raises(LLMError):
            await router.chat([Message(role="user", content="x")])


class TestUsageCapture:
    async def test_capture_records_routed_model_tokens_and_task(self) -> None:
        adapter = _FakeAdapter("openai")
        router = _router_with(adapter)

        async with router.capture_usage() as records:
            await router.chat(
                [Message(role="user", content="x")],
                model_hint="openai:gpt-4o-mini",
                task_type="plan",
            )

        assert len(records) == 1
        assert records[0].provider == "openai"
        assert records[0].model == "gpt-4o-mini"
        assert records[0].task_type == "plan"
        assert records[0].prompt_tokens == 7
        assert records[0].completion_tokens == 3
        assert records[0].total_tokens == 10
        assert records[0].success is True

    async def test_capture_context_is_restored_after_exit(self) -> None:
        adapter = _FakeAdapter("openai")
        router = _router_with(adapter)

        async with router.capture_usage() as records:
            await router.chat([Message(role="user", content="inside")])
        await router.chat([Message(role="user", content="outside")])

        assert len(records) == 1
