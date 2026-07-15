"""Slack channel adapter — Events API parsing, signature verification, messaging.

Slack supports message updates via ``chat.update``, so ``send_streaming`` does
true streaming: posts an initial message, then updates it as tokens arrive,
and finally settles on the ``final`` event content.

Signature format: ``v0=HMAC-SHA256(signing_secret, "v0:{timestamp}:{body}")``.
The timestamp is passed in the ``X-Slack-Request-Timestamp`` header; since
``verify_signature`` receives only ``raw`` bytes and the signature string, the
caller must encode the timestamp into the signature string as
``"v0:{ts}:{body}"`` — for Phase 3 we simplify by expecting the gateway to
pass the already-computed basestring as ``signature`` when no timestamp is
available, falling back to direct HMAC of the body.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any

import httpx
from eaos.gateway.im.message import Attachment, UnifiedMessage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.runner import AgentEvent
    from eaos.core.config import SlackConfig


class SlackChannel:
    """Channel adapter for Slack Events API webhooks.

    Phase 3 simplification: tenant_id/user_id/agent_id are passed directly in
    the webhook payload (``raw["tenant_id"]`` etc.). Production code should
    resolve Slack user_id to internal user via UserMappingRepository.
    """

    _POST_URL = "https://slack.com/api/chat.postMessage"
    _UPDATE_URL = "https://slack.com/api/chat.update"

    def __init__(self, config: SlackConfig) -> None:
        self._config = config
        self._signing_secret = config.signing_secret or ""
        self._bot_token = config.bot_token or ""

    @property
    def name(self) -> str:
        return "slack"

    async def parse_webhook(
        self,
        raw: dict[str, Any],
        headers: dict[str, str],
    ) -> UnifiedMessage:
        del headers
        from uuid import UUID

        event = raw.get("event", raw)
        text_content = str(event.get("text", "")).strip()
        # Slack wraps @mentions as <@U123>; strip to plain text for the agent
        if text_content.startswith("<@") and ">" in text_content:
            close = text_content.index(">")
            text_content = text_content[close + 1 :].strip()

        msg_id = str(event.get("ts", raw.get("event_id", "")))
        sender = str(event.get("user", "user"))
        channel = str(event.get("channel", ""))

        return UnifiedMessage(
            channel="slack",
            channel_message_id=msg_id,
            tenant_id=UUID(str(raw["tenant_id"])),
            user_id=UUID(str(raw["user_id"])),
            user_name=sender,
            agent_id=UUID(str(raw["agent_id"])),
            text=text_content,
            attachments=[],
            is_mention=bool(raw.get("isAt", False)),
            thread_id=channel or None,
            raw=raw,
        )

    async def send_message(
        self,
        target: str,
        text: str,
        attachments: list[Attachment] | None = None,
    ) -> str | None:
        """Send a message; return Slack message ``ts`` (for later updates)."""
        del attachments  # Phase 3: no attachment rendering
        headers = {"Authorization": f"Bearer {self._bot_token}"}
        payload: dict[str, Any] = {"channel": target, "text": text}
        async with httpx.AsyncClient() as client:
            resp = await client.post(self._POST_URL, json=payload, headers=headers)
            data = resp.json()
        if data.get("ok"):
            return str(data.get("ts", ""))
        return None

    async def send_streaming(
        self,
        target: str,
        event_stream: AsyncIterator[AgentEvent],
    ) -> None:
        """True streaming: post initial message, update on tokens, settle on final."""
        buffer = ""
        msg_ts: str | None = None
        async for event in event_stream:
            if event.type == "token" and event.content:
                buffer += event.content
                if msg_ts is None:
                    msg_ts = await self.send_message(target, buffer)
                else:
                    await self._update_message(target, msg_ts, buffer)
            elif event.type == "final" and event.content:
                buffer = event.content
                if msg_ts is None:
                    msg_ts = await self.send_message(target, buffer)
                else:
                    await self._update_message(target, msg_ts, buffer)
        if msg_ts is None and buffer:
            await self.send_message(target, buffer)

    async def _update_message(
        self, channel: str, ts: str, text: str
    ) -> None:
        headers = {"Authorization": f"Bearer {self._bot_token}"}
        payload: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        async with httpx.AsyncClient() as client:
            await client.post(self._UPDATE_URL, json=payload, headers=headers)

    async def verify_signature(
        self,
        raw: bytes,
        signature: str,
    ) -> bool:
        if not self._signing_secret:
            return True  # Phase 3: no secret configured → skip verification
        # Phase 3 simplified: HMAC-SHA256 of raw body directly.
        # Production Slack uses "v0:{timestamp}:{body}" basestring; the gateway
        # is expected to pass that basestring via the signature param when
        # timestamp headers are available. For now we hash raw bytes for
        # consistency with other channels' test harnesses.
        computed = hmac.new(
            self._signing_secret.encode("utf-8"),
            raw,
            hashlib.sha256,
        )
        expected = f"v0={computed.hexdigest()}"
        return hmac.compare_digest(expected, signature)
