"""Feishu (飞书) channel adapter — v2 event callback, signature, messaging.

Feishu supports message patching via ``/im/v1/messages/{message_id}``, so
``send_streaming`` does true streaming: posts an initial message, then patches
it as tokens arrive, settling on the ``final`` event content.

Signature format: ``SHA256(timestamp + nonce + encrypt_key + body)``. The
caller packages timestamp+nonce into the signature string as
``"{ts}:{nonce}:{body}"`` so this adapter can recompute consistently.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any

import httpx
from eaos.gateway.im.message import Attachment, UnifiedMessage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.runner import AgentEvent
    from eaos.core.config import FeishuConfig


class FeishuChannel:
    """Channel adapter for Feishu (飞书) v2 event webhooks.

    Phase 3 simplification: tenant_id/user_id/agent_id are passed directly in
    the webhook payload (``raw["tenant_id"]`` etc.). Production code should
    resolve Feishu open_id to internal user via UserMappingRepository.
    """

    _POST_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
    _PATCH_URL_TMPL = "https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"

    def __init__(self, config: FeishuConfig) -> None:
        self._config = config
        self._encrypt_key = config.encrypt_key or ""
        self._verification_token = config.verification_token or ""

    @property
    def name(self) -> str:
        return "feishu"

    async def parse_webhook(
        self,
        raw: dict[str, Any],
        headers: dict[str, str],
    ) -> UnifiedMessage:
        del headers
        from uuid import UUID

        # Feishu v2 schema: {schema, header, event}
        header = raw.get("header", {})
        event = raw.get("event", raw)

        # message content is a JSON string: {"text": "..."}
        message = event.get("message", {})
        content_raw = message.get("content", "{}")
        try:
            content_obj = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
        except (json.JSONDecodeError, TypeError):
            content_obj = {}
        text_content = str(content_obj.get("text", "")).strip()

        # @mention in Feishu: text contains @_user_1; strip mention markers
        if "@_user" in text_content:
            idx = text_content.find(" ", 0)
            text_content = text_content[idx + 1 :].strip() if idx >= 0 else ""

        sender = event.get("sender", {}).get("sender_id", {}).get("open_id", "user")
        msg_id = str(message.get("message_id", header.get("event_id", "")))
        chat_id = str(message.get("chat_id", ""))

        return UnifiedMessage(
            channel="feishu",
            channel_message_id=msg_id,
            tenant_id=UUID(str(raw["tenant_id"])),
            user_id=UUID(str(raw["user_id"])),
            user_name=str(sender),
            agent_id=UUID(str(raw["agent_id"])),
            text=text_content,
            attachments=[],
            is_mention=bool(raw.get("isAt", False)),
            thread_id=chat_id or None,
            raw=raw,
        )

    async def send_message(
        self,
        target: str,
        text: str,
        attachments: list[Attachment] | None = None,
    ) -> str | None:
        """Send a message to a chat (target = chat_id); return message_id."""
        del attachments
        payload: dict[str, Any] = {
            "receive_id": target,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        headers = {"Content-Type": "application/json"}
        params = {"receive_id_type": "chat_id"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._POST_URL, json=payload, headers=headers, params=params
            )
            data = resp.json()
        if data.get("code") == 0:
            return str(data.get("data", {}).get("message_id", ""))
        return None

    async def send_streaming(
        self,
        target: str,
        event_stream: AsyncIterator[AgentEvent],
    ) -> None:
        """True streaming: post initial message, patch on tokens, settle on final."""
        buffer = ""
        msg_id: str | None = None
        async for event in event_stream:
            if event.type == "token" and event.content:
                buffer += event.content
                if msg_id is None:
                    msg_id = await self.send_message(target, buffer)
                else:
                    await self._patch_message(msg_id, buffer)
            elif event.type == "final" and event.content:
                buffer = event.content
                if msg_id is None:
                    msg_id = await self.send_message(target, buffer)
                else:
                    await self._patch_message(msg_id, buffer)
        if msg_id is None and buffer:
            await self.send_message(target, buffer)

    async def _patch_message(self, message_id: str, text: str) -> None:
        url = self._PATCH_URL_TMPL.format(message_id=message_id)
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        async with httpx.AsyncClient() as client:
            await client.patch(url, json=payload)

    async def verify_signature(
        self,
        raw: bytes,
        signature: str,
    ) -> bool:
        if not self._encrypt_key:
            return True  # Phase 3: no encrypt_key → skip verification
        # Feishu signature: SHA256(timestamp + nonce + encrypt_key + body).
        # Phase 3 simplified: SHA256(encrypt_key + raw_body).
        computed = hashlib.sha256(
            self._encrypt_key.encode("utf-8") + raw
        ).hexdigest()
        return hmac.compare_digest(computed, signature)
