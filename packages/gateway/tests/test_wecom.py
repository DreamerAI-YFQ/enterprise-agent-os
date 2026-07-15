"""Tests for WeComChannel — webhook parsing, messaging, and signature verification.

httpx is mocked via AsyncMock to verify HTTP payloads without real network calls.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from eaos.core.config import WeComConfig
from eaos.gateway.im.channels.wecom import WeComChannel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.runner import AgentEvent


def _config(
    *,
    token: str | None = "test-token",
    corp_id: str | None = "test-key",
) -> WeComConfig:
    return WeComConfig(
        corp_id=corp_id,
        agent_id=None,
        secret=None,
        token=token,
        encoding_aes_key=None,
    )


def _webhook_payload(
    *,
    text: str = "hello @agent",
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    agent_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "MsgId": "wecom-msg-001",
        "MsgType": "text",
        "Content": text,
        "FromUserName": "alice-id",
        "chatid": "group-abc",
        "isAt": True,
        "tenant_id": str(tenant_id or uuid4()),
        "user_id": str(user_id or uuid4()),
        "agent_id": str(agent_id or uuid4()),
    }


class TestParseWebhook:
    async def test_extracts_text_and_ids(self) -> None:
        channel = WeComChannel(_config())
        tenant_id = uuid4()
        user_id = uuid4()
        agent_id = uuid4()
        raw = _webhook_payload(
            text="查询库存", tenant_id=tenant_id, user_id=user_id, agent_id=agent_id
        )

        msg = await channel.parse_webhook(raw, {})

        assert msg.channel == "wecom"
        assert msg.channel_message_id == "wecom-msg-001"
        assert msg.tenant_id == tenant_id
        assert msg.user_id == user_id
        assert msg.agent_id == agent_id
        assert msg.text == "查询库存"
        assert msg.user_name == "alice-id"
        assert msg.is_mention is True
        assert msg.thread_id == "group-abc"

    async def test_strips_text_whitespace(self) -> None:
        channel = WeComChannel(_config())
        raw = _webhook_payload(text="  hello  ")

        msg = await channel.parse_webhook(raw, {})

        assert msg.text == "hello"


class TestSendMessage:
    async def test_posts_text_payload_to_wecom(self) -> None:
        channel = WeComChannel(_config())
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)

            await channel.send_message("target-1", "hello world")

            mock_client.post.assert_awaited_once()
            call_args = mock_client.post.call_args
            url = call_args.args[0] if call_args.args else call_args.kwargs["url"]
            assert "qyapi.weixin.qq.com/cgi-bin/webhook/send" in url
            assert "key=test-key" in url
            payload = call_args.kwargs["json"]
            assert payload["msgtype"] == "text"
            assert payload["text"]["content"] == "hello world"


class TestSendStreaming:
    async def test_sends_processing_then_final(self) -> None:
        channel = WeComChannel(_config())
        sent_messages: list[str] = []

        async def _fake_send(target: str, text: str, attachments: Any = None) -> None:
            sent_messages.append(text)

        channel.send_message = _fake_send  # type: ignore[method-assign]

        async def _event_stream() -> AsyncIterator[AgentEvent]:
            from eaos.agent.runner import AgentEvent

            yield AgentEvent(type="token", content="thinking")
            yield AgentEvent(type="final", content="final answer")

        await channel.send_streaming("target-1", _event_stream())

        assert len(sent_messages) == 2
        assert sent_messages[0] == "处理中..."
        assert sent_messages[1] == "final answer"

    async def test_no_final_sends_only_processing(self) -> None:
        channel = WeComChannel(_config())
        sent_messages: list[str] = []

        async def _fake_send(target: str, text: str, attachments: Any = None) -> None:
            sent_messages.append(text)

        channel.send_message = _fake_send  # type: ignore[method-assign]

        async def _event_stream() -> AsyncIterator[AgentEvent]:
            from eaos.agent.runner import AgentEvent

            yield AgentEvent(type="token", content="thinking")

        await channel.send_streaming("target-1", _event_stream())

        assert len(sent_messages) == 1
        assert sent_messages[0] == "处理中..."


class TestVerifySignature:
    async def test_valid_signature_passes(self) -> None:
        token = "my-token"
        channel = WeComChannel(_config(token=token))
        raw = b'{"Content":"hello"}'

        computed = hmac.new(token.encode("utf-8"), raw, hashlib.sha256)
        signature = computed.hexdigest()

        result = await channel.verify_signature(raw, signature)
        assert result is True

    async def test_invalid_signature_fails(self) -> None:
        channel = WeComChannel(_config(token="my-token"))

        result = await channel.verify_signature(b'{"Content":"hello"}', "wrong-sig")
        assert result is False

    async def test_no_token_skips_verification(self) -> None:
        channel = WeComChannel(_config(token=None))

        result = await channel.verify_signature(b'{"Content":"hello"}', "")
        assert result is True


class TestChannelName:
    def test_name_is_wecom(self) -> None:
        channel = WeComChannel(_config())
        assert channel.name == "wecom"
