"""Tests for MessageGatewayImpl — webhook routing, signature check, and dispatch.

Channel and Orchestrator are mocked. The orchestrator mock uses a real async
generator so the event stream flows through to send_streaming.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from eaos.gateway.im.gateway import MessageGatewayImpl

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.orchestrator import AgentOrchestrator
    from eaos.agent.runner import AgentEvent
    from eaos.core.context import TenantContext
    from eaos.gateway.im.channels.base import Channel
    from eaos.gateway.im.message import UnifiedMessage


class _MockChannel:
    """Channel mock with configurable signature check and parse output."""

    def __init__(
        self,
        *,
        name: str = "mock",
        signature_valid: bool = True,
        message: UnifiedMessage | None = None,
    ) -> None:
        self.name = name
        self._signature_valid = signature_valid
        self._message = message
        self.send_streaming_calls: list[tuple[str, Any]] = []

    async def verify_signature(self, raw: bytes, signature: str) -> bool:
        return self._signature_valid

    async def parse_webhook(
        self,
        raw: dict[str, Any],
        headers: dict[str, str],
    ) -> UnifiedMessage:
        if self._message is not None:
            return self._message
        from eaos.gateway.im.message import UnifiedMessage

        return UnifiedMessage(
            channel=self.name,
            channel_message_id="msg-123",
            tenant_id=raw["tenant_id"],
            user_id=raw["user_id"],
            user_name="tester",
            agent_id=raw["agent_id"],
            text=raw.get("text", "hello"),
        )

    async def send_streaming(
        self,
        target: str,
        event_stream: AsyncIterator[AgentEvent],
    ) -> None:
        self.send_streaming_calls.append((target, event_stream))
        async for _event in event_stream:
            pass


class _MockOrchestrator:
    """AgentOrchestrator mock whose execute is a real async generator."""

    def __init__(self, *, final_content: str = "orchestrator-final") -> None:
        self.final_content = final_content
        self.execute_calls: list[TenantContext] = []
        self.execute_messages: list[str] = []

    async def execute(
        self,
        ctx: TenantContext,
        user_message: str,
    ) -> AsyncIterator[AgentEvent]:
        self.execute_calls.append(ctx)
        self.execute_messages.append(user_message)
        from eaos.agent.runner import AgentEvent

        yield AgentEvent(type="token", content="thinking", agent_id=ctx.agent_id)
        yield AgentEvent(
            type="final", content=self.final_content, agent_id=ctx.agent_id
        )


def _channel(c: _MockChannel) -> Channel:
    return cast("Channel", c)


def _orchestrator(o: _MockOrchestrator) -> AgentOrchestrator:
    return cast("AgentOrchestrator", o)


def _make_gateway(
    *,
    orchestrator: _MockOrchestrator | None = None,
) -> tuple[MessageGatewayImpl, _MockOrchestrator]:
    orch = orchestrator or _MockOrchestrator()
    gw = MessageGatewayImpl(orchestrator=_orchestrator(orch))
    return gw, orch


def _webhook_raw() -> dict[str, Any]:
    return {
        "tenant_id": uuid4(),
        "user_id": uuid4(),
        "agent_id": uuid4(),
        "text": "hello agent",
    }


class TestHandleWebhookHappyPath:
    async def test_accepted_response_with_message_id(self) -> None:
        gw, _orch = _make_gateway()
        channel = _MockChannel(name="mock")
        gw.register_channel(_channel(channel))

        raw = _webhook_raw()
        response = await gw.handle_webhook("mock", raw, {"signature": "sig"})

        assert response["status"] == "accepted"
        assert response["message_id"] == "msg-123"

    async def test_dispatch_runs_orchestrator_and_streaming(self) -> None:
        gw, orch = _make_gateway()
        channel = _MockChannel(name="mock")
        gw.register_channel(_channel(channel))

        raw = _webhook_raw()
        await gw.handle_webhook("mock", raw, {"signature": "sig"})
        await asyncio.sleep(0)

        assert len(orch.execute_calls) == 1
        assert orch.execute_messages[0] == "hello agent"
        assert len(channel.send_streaming_calls) == 1
        target, _stream = channel.send_streaming_calls[0]
        assert target == "msg-123"


class TestUnknownChannel:
    async def test_returns_404_for_unregistered_channel(self) -> None:
        gw, _orch = _make_gateway()

        response = await gw.handle_webhook("unknown", _webhook_raw(), {})

        assert response["status"] == "error"
        assert response["code"] == 404
        assert "unknown channel" in response["message"]


class TestInvalidSignature:
    async def test_returns_401_on_signature_failure(self) -> None:
        gw, _orch = _make_gateway()
        channel = _MockChannel(name="mock", signature_valid=False)
        gw.register_channel(_channel(channel))

        response = await gw.handle_webhook("mock", _webhook_raw(), {"signature": "bad"})

        assert response["status"] == "error"
        assert response["code"] == 401
        await asyncio.sleep(0)
        assert len(channel.send_streaming_calls) == 0


class TestRegisterChannel:
    async def test_register_replaces_by_name(self) -> None:
        gw, _orch = _make_gateway()
        ch1 = _MockChannel(name="mock")
        ch2 = _MockChannel(name="mock")

        gw.register_channel(_channel(ch1))
        gw.register_channel(_channel(ch2))

        await gw.handle_webhook("mock", _webhook_raw(), {"signature": "sig"})
        await asyncio.sleep(0)
        assert len(ch2.send_streaming_calls) == 1
        assert len(ch1.send_streaming_calls) == 0
