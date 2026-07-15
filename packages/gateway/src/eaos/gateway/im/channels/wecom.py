"""WeCom (企业微信) channel adapter.

WeCom does not support true streaming, so ``send_streaming`` sends a
"处理中..." message first, then appends the final result — same pattern as
DingTalk. Signature verification uses HMAC-SHA256 with the callback ``token``
(Phase 3 simplification; production WeCom requires AES decryption via
``encoding_aes_key``).
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
    from eaos.core.config import WeComConfig


class WeComChannel:
    """Channel adapter for WeCom (企业微信) IM webhooks.

    Phase 3 simplification: tenant_id/user_id/agent_id are passed directly in
    the webhook payload (``raw["tenant_id"]`` etc.). Production code should
    resolve these from WeCom's userid via UserMappingRepository.
    """

    def __init__(self, config: WeComConfig) -> None:
        self._config = config
        self._token = config.token or ""
        webhook = config.corp_id or ""
        # WeCom group robot webhook URL is configured per-bot; corp_id is used
        # as a placeholder key in Phase 3 (production reads from a bot map).
        self._webhook_url = (
            f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={webhook}"
        )

    @property
    def name(self) -> str:
        return "wecom"

    async def parse_webhook(
        self,
        raw: dict[str, Any],
        headers: dict[str, str],
    ) -> UnifiedMessage:
        del headers  # WeCom parse doesn't need headers in Phase 3
        from uuid import UUID

        text_content = str(raw.get("Content", raw.get("content", ""))).strip()
        msg_id = str(raw.get("MsgId", raw.get("msgid", "")))
        sender = str(raw.get("FromUserName", raw.get("userid", "user")))

        return UnifiedMessage(
            channel="wecom",
            channel_message_id=msg_id,
            tenant_id=UUID(str(raw["tenant_id"])),
            user_id=UUID(str(raw["user_id"])),
            user_name=sender,
            agent_id=UUID(str(raw["agent_id"])),
            text=text_content,
            attachments=[],
            is_mention=bool(raw.get("isAt", False)),
            thread_id=str(raw.get("chatid", "")) or None,
            raw=raw,
        )

    async def send_message(
        self,
        target: str,
        text: str,
        attachments: list[Attachment] | None = None,
    ) -> None:
        del target  # WeCom robot sends to the group that owns the webhook
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
        if not self._token:
            return True  # Phase 3: no token configured → skip verification
        computed = hmac.new(
            self._token.encode("utf-8"),
            raw,
            hashlib.sha256,
        )
        expected = computed.hexdigest()
        return hmac.compare_digest(expected, signature)
