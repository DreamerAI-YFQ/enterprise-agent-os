"""Channel protocol — adapter for a specific IM (Slack/DingTalk/etc).

Each channel (DingTalk, Slack, WeCom, Feishu) implements this. The
MessageGateway routes webhooks to the appropriate channel adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.runner import AgentEvent
    from eaos.gateway.im.message import Attachment, UnifiedMessage


class Channel(Protocol):
    """IM channel adapter protocol."""

    @property
    def name(self) -> str:
        """Channel identifier (dingtalk/wecom/slack/feishu)."""
        ...

    async def parse_webhook(
        self,
        raw: dict[str, Any],
        headers: dict[str, str],
    ) -> UnifiedMessage:
        """Convert channel webhook payload to UnifiedMessage."""
        ...

    async def send_message(
        self,
        target: str,
        text: str,
        attachments: list[Attachment] | None = None,
    ) -> None:
        """Send a message to a channel target (group/user/thread)."""
        ...

    async def send_streaming(
        self,
        target: str,
        event_stream: AsyncIterator[AgentEvent],
    ) -> None:
        """Stream agent events to channel.

        Channels supporting message update (Slack, Feishu) do true streaming;
        others (DingTalk, WeCom) send 'processing...' then append final result.
        """
        ...

    async def verify_signature(
        self,
        raw: bytes,
        signature: str,
    ) -> bool:
        """Verify webhook signature to prevent spoofing."""
        ...
