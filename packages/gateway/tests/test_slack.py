"""Tests for SlackChannel — Events API parsing, messaging, streaming, signature.

Slack supports message updates (chat.update), so streaming tests verify the
update path. httpx is mocked via AsyncMock.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from eaos.core.config import SlackConfig
from eaos.gateway.im.channels.slack import SlackChannel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.runner import AgentEvent


def _config(
    *,
    bot_token: str | None = "xoxb-test-token",
    signing_secret: str | None = "test-secret",
) -> SlackConfig:
    return SlackConfig(bot_token=bot_token, signing_secret=signing_secret)


def _webhook_payload(
    *,
    text: str = "hello @agent",
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    agent_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "event_id": "slack-evt-001",
        "event": {
            "type": "message",
            "ts": "1355517523.000005",
            "user": "U123",
            "channel": "C456",
            "text": text,
        },
        "tenant_id": str(tenant_id or uuid4()),
        "user_id": str(user_id or uuid4()),
        "agent_id": str(agent_id or uuid4()),
    }


class TestParseWebhook:
    async def test_extracts_text_and_ids(self) -> None:
        channel = SlackChannel(_config())
        tenant_id = uuid4()
        user_id = uuid4()
        agent_id = uuid4()
        raw = _webhook_payload(
            text="查询库存", tenant_id=tenant_id, user_id=user_id, agent_id=agent_id
        )

        msg = await channel.parse_webhook(raw, {})

        assert msg.channel == "slack"
        assert msg.channel_message_id == "1355517523.000005"
        assert msg.tenant_id == tenant_id
        assert msg.user_id == user_id
        assert msg.agent_id == agent_id
        assert msg.text == "查询库存"
        assert msg.user_name == "U123"
        assert msg.thread_id == "C456"

    async def test_strips_mention_prefix(self) -> None:
        channel = SlackChannel(_config())
        raw = _webhook_payload(text="<@U123> hello there")

        msg = await channel.parse_webhook(raw, {})

        assert msg.text == "hello there"


class TestSendMessage:
    async def test_posts_to_slack_api(self) -> None:
        channel = SlackChannel(_config())
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "ts": "1234567890.000001"}
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)

            ts = await channel.send_message("C456", "hello world")

            mock_client.post.assert_awaited_once()
            call_args = mock_client.post.call_args
            url = call_args.args[0] if call_args.args else call_args.kwargs["url"]
            assert "slack.com/api/chat.postMessage" in url
            payload = call_args.kwargs["json"]
            assert payload["channel"] == "C456"
            assert payload["text"] == "hello world"
            headers = call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer xoxb-test-token"
            assert ts == "1234567890.000001"

    async def test_returns_none_on_failure(self) -> None:
        channel = SlackChannel(_config())
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": False, "error": "rate_limited"}
        mock_response.status_code = 429

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)

            ts = await channel.send_message("C456", "hello")
            assert ts is None


class TestSendStreaming:
    async def test_true_streaming_updates_message(self) -> None:
        """Slack streaming: post initial, update on tokens, settle on final."""
        channel = SlackChannel(_config())
        sent: list[tuple[str, str]] = []  # (text, kind)
        update_calls: list[str] = []

        async def _fake_send(target: str, text: str, attachments: Any = None) -> str:
            sent.append((text, "post"))
            return "ts-001"

        async def _fake_update(channel_id: str, ts: str, text: str) -> None:
            del channel_id, ts
            update_calls.append(text)

        channel.send_message = _fake_send  # type: ignore[method-assign]
        channel._update_message = _fake_update  # type: ignore[method-assign, assignment]

        async def _event_stream() -> AsyncIterator[AgentEvent]:
            from eaos.agent.runner import AgentEvent

            yield AgentEvent(type="token", content="Hello")
            yield AgentEvent(type="token", content=" world")
            yield AgentEvent(type="final", content="Hello world final")

        await channel.send_streaming("C456", _event_stream())

        # First token posts a new message
        assert sent[0] == ("Hello", "post")
        # Second token + final update the existing message
        assert update_calls == ["Hello world", "Hello world final"]

    async def test_final_only_posts_once(self) -> None:
        channel = SlackChannel(_config())
        sent: list[str] = []

        async def _fake_send(target: str, text: str, attachments: Any = None) -> str:
            sent.append(text)
            return "ts-001"

        async def _fake_update(channel_id: str, ts: str, text: str) -> None:
            sent.append(f"update:{text}")

        channel.send_message = _fake_send  # type: ignore[method-assign]
        channel._update_message = _fake_update  # type: ignore[method-assign, assignment]

        async def _event_stream() -> AsyncIterator[AgentEvent]:
            from eaos.agent.runner import AgentEvent

            yield AgentEvent(type="final", content="just final")

        await channel.send_streaming("C456", _event_stream())

        assert sent == ["just final"]


class TestVerifySignature:
    async def test_valid_signature_passes(self) -> None:
        secret = "my-secret"
        channel = SlackChannel(_config(signing_secret=secret))
        raw = b'{"event":"message"}'

        computed = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256)
        signature = f"v0={computed.hexdigest()}"

        result = await channel.verify_signature(raw, signature)
        assert result is True

    async def test_invalid_signature_fails(self) -> None:
        channel = SlackChannel(_config(signing_secret="my-secret"))

        result = await channel.verify_signature(b'{"event":"message"}', "v0=wrong")
        assert result is False

    async def test_no_secret_skips_verification(self) -> None:
        channel = SlackChannel(_config(signing_secret=None))

        result = await channel.verify_signature(b'{"event":"message"}', "")
        assert result is True


class TestChannelName:
    def test_name_is_slack(self) -> None:
        channel = SlackChannel(_config())
        assert channel.name == "slack"
