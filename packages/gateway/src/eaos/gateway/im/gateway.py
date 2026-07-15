"""Message gateway — routes webhooks to channels."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.orchestrator import AgentOrchestrator
    from eaos.agent.runner import AgentEvent
    from eaos.agent.tenant import TenantManager
    from eaos.core.context import TenantContext
    from eaos.gateway.im.channels.base import Channel
    from eaos.gateway.im.message import UnifiedMessage


class MessageGateway(Protocol):
    """Unified message gateway routing webhooks to channels."""

    async def handle_webhook(
        self,
        channel_name: str,
        raw: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """Handle incoming webhook from any channel.

        Verifies signature -> parses to UnifiedMessage -> checks @mention ->
        builds TenantContext -> invokes AgentOrchestrator async -> returns 200.
        """
        ...

    def register_channel(self, channel: Channel) -> None:
        """Register a channel adapter."""
        ...


def _aiter(coro: Any) -> AsyncIterator[AgentEvent]:
    """Cast a coroutine/async-gen result to AsyncIterator for ``async for``.

    ``AgentOrchestrator.execute`` is declared ``async def -> AsyncIterator``
    in the Protocol, which mypy reads as a coroutine; concrete impls are
    async generators returning AsyncIterator directly. The cast bridges this.
    """
    return cast("AsyncIterator[AgentEvent]", coro)


class MessageGatewayImpl:
    """MessageGateway backed by channel adapters + AgentOrchestrator.

    Webhooks are dispatched asynchronously so the HTTP response returns
    immediately (200 accepted). The orchestrator event stream is passed to
    the channel's ``send_streaming`` for real-time delivery to the user.
    """

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        tenant_manager: TenantManager | None = None,
    ) -> None:
        self._channels: dict[str, Channel] = {}
        self._orchestrator = orchestrator
        self._tenant_manager = tenant_manager

    def register_channel(self, channel: Channel) -> None:
        self._channels[channel.name] = channel

    async def handle_webhook(
        self,
        channel_name: str,
        raw: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        channel = self._channels.get(channel_name)
        if channel is None:
            return {
                "status": "error",
                "code": 404,
                "message": f"unknown channel: {channel_name}",
            }

        signature = headers.get("signature", "")
        raw_bytes = json.dumps(raw, default=str, ensure_ascii=False).encode("utf-8")
        if not await channel.verify_signature(raw_bytes, signature):
            return {
                "status": "error",
                "code": 401,
                "message": "invalid signature",
            }

        msg = await channel.parse_webhook(raw, headers)
        ctx = self._build_context(msg)
        asyncio.create_task(self._dispatch(channel, msg, ctx))
        return {"status": "accepted", "message_id": msg.channel_message_id}

    @staticmethod
    def _build_context(msg: UnifiedMessage) -> TenantContext:
        from eaos.core.context import TenantContext

        return TenantContext(
            tenant_id=msg.tenant_id,
            user_id=msg.user_id,
            agent_id=msg.agent_id,
            agent_scope="company",
        )

    async def _dispatch(
        self,
        channel: Channel,
        msg: UnifiedMessage,
        ctx: TenantContext,
    ) -> None:
        event_stream = _aiter(self._orchestrator.execute(ctx, msg.text))
        await channel.send_streaming(msg.channel_message_id, event_stream)
