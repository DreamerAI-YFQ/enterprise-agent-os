"""DingTalk channel adapter — webhook parsing, signature verification, and messaging.

DingTalk does not support true streaming, so ``send_streaming`` sends a
"processing..." message first, then collects the final event and appends it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import TYPE_CHECKING, Any

import httpx
from eaos.gateway.im.message import Attachment, UnifiedMessage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.runner import AgentEvent
    from eaos.core.config import DingTalkConfig


class DingTalkChannel:
    """Channel adapter for DingTalk IM webhooks.

    Phase 3 simplification: tenant_id/user_id/agent_id are passed directly in
    the webhook payload (``raw["tenant_id"]`` etc.). Production code should
    resolve these from DingTalk's token/session instead.
    """

    def __init__(self, config: DingTalkConfig) -> None:
        self._config = config
        self._app_secret = config.app_secret or ""
        token = config.app_key or ""
        self._webhook_url = (
            f"https://oapi.dingtalk.com/robot/send?access_token={token}"
        )

    @property
    def name(self) -> str:
        return "dingtalk"

    async def parse_webhook(
        self,
        raw: dict[str, Any],
        headers: dict[str, str],
    ) -> UnifiedMessage:
        del headers  # DingTalk parse doesn't need headers in Phase 3
        from uuid import UUID

        text_content = ""
        text_obj = raw.get("text")
        if isinstance(text_obj, dict):
            text_content = str(text_obj.get("content", "")).strip()

        return UnifiedMessage(
            channel="dingtalk",
            channel_message_id=str(raw.get("msgId", raw.get("messageId", ""))),
            tenant_id=UUID(str(raw["tenant_id"])),
            user_id=UUID(str(raw["user_id"])),
            user_name=str(raw.get("senderNick", raw.get("senderId", "user"))),
            agent_id=UUID(str(raw["agent_id"])),
            text=text_content,
            attachments=[],
            is_mention=bool(raw.get("isAt", False)),
            thread_id=str(raw.get("conversationId", "")) or None,
            raw=raw,
        )

    async def send_message(
        self,
        target: str,
        text: str,
        attachments: list[Attachment] | None = None,
    ) -> None:
        del target  # DingTalk robot sends to the group that owns the webhook
        payload: dict[str, Any] = {"msgtype": "text", "text": {"content": text}}
        async with httpx.AsyncClient() as client:
            await client.post(self._webhook_url, json=payload)

    async def send_streaming(
        self,
        target: str,
        event_stream: AsyncIterator[AgentEvent],
    ) -> None:
        await self.send_message(target, "处理中...")
        final_content = ""
        async for event in event_stream:
            if event.type == "final" and event.content:
                final_content = event.content
        if final_content:
            await self.send_message(target, final_content)

    async def verify_signature(
        self,
        raw: bytes,
        signature: str,
    ) -> bool:
        if not self._app_secret:
            return True  # Phase 3: no secret configured → skip verification
        computed = hmac.new(
            self._app_secret.encode("utf-8"),
            raw,
            hashlib.sha256,
        )
        expected = base64.b64encode(computed.digest()).decode("utf-8")
        return hmac.compare_digest(expected, signature)
