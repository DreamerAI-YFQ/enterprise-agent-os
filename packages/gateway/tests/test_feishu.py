"""Tests for FeishuChannel — v2 event parsing, messaging, streaming, signature.

Feishu supports message patching, so streaming tests verify the patch path.
httpx is mocked via AsyncMock.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from eaos.core.config import FeishuConfig
from eaos.gateway.im.channels.feishu import FeishuChannel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.runner import AgentEvent


def _config(
    *,
    encrypt_key: str | None = "test-encrypt-key",
    verification_token: str | None = "test-token",
) -> FeishuConfig:
    return FeishuConfig(
        app_id="test-app-id",
        app_secret=None,
        verification_token=verification_token,
        encrypt_key=encrypt_key,
    )


def _webhook_payload(
    *,
    text: str = "hello @agent",
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    agent_id: UUID | None = None,
) -> dict[str, Any]:
    import json

    return {
        "schema": "2.0",
        "header": {
            "event_id": "feishu-evt-001",
            "event_type": "im.message.receive_v1",
            "token": "test-token",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_abc"}},
            "message": {
                "message_id": "om_001",
                "chat_id": "oc_group1",
                "message_type": "text",
                "content": json.dumps({"text": text}),
            },
        },
        "tenant_id": str(tenant_id or uuid4()),
        "user_id": str(user_id or uuid4()),
        "agent_id": str(agent_id or uuid4()),
    }


class TestParseWebhook:
    async def test_extracts_text_and_ids(self) -> None:
        channel = FeishuChannel(_config())
        tenant_id = uuid4()
        user_id = uuid4()
        agent_id = uuid4()
        raw = _webhook_payload(
            text="查询库存", tenant_id=tenant_id, user_id=user_id, agent_id=agent_id
        )

        msg = await channel.parse_webhook(raw, {})

        assert msg.channel == "feishu"
        assert msg.channel_message_id == "om_001"
        assert msg.tenant_id == tenant_id
        assert msg.user_id == user_id
        assert msg.agent_id == agent_id
        assert msg.text == "查询库存"
        assert msg.user_name == "ou_abc"
        assert msg.thread_id == "oc_group1"

    async def test_strips_mention_marker(self) -> None:
        channel = FeishuChannel(_config())
        raw = _webhook_payload(text="@_user_1 hello there")

        msg = await channel.parse_webhook(raw, {})

        assert msg.text == "hello there"

    async def test_handles_invalid_content_json(self) -> None:
        channel = FeishuChannel(_config())
        raw = _webhook_payload()
        # corrupt the content field
        raw["event"]["message"]["content"] = "not-json"

        msg = await channel.parse_webhook(raw, {})

        assert msg.text == ""


class TestSendMessage:
    async def test_posts_to_feishu_api(self) -> None:
        channel = FeishuChannel(_config())
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 0,
            "data": {"message_id": "om_new001"},
        }
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)

            msg_id = await channel.send_message("oc_group1", "hello world")

            mock_client.post.assert_awaited_once()
            call_args = mock_client.post.call_args
            url = call_args.args[0] if call_args.args else call_args.kwargs["url"]
            assert "open.feishu.cn/open-apis/im/v1/messages" in url
            payload = call_args.kwargs["json"]
            assert payload["receive_id"] == "oc_group1"
            assert payload["msg_type"] == "text"
            params = call_args.kwargs["params"]
            assert params["receive_id_type"] == "chat_id"
            assert msg_id == "om_new001"

    async def test_returns_none_on_failure(self) -> None:
        channel = FeishuChannel(_config())
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 9499, "msg": "invalid token"}
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)

            msg_id = await channel.send_message("oc_group1", "hello")
            assert msg_id is None


class TestSendStreaming:
    async def test_true_streaming_patches_message(self) -> None:
        """Feishu streaming: post initial, patch on tokens, settle on final."""
        channel = FeishuChannel(_config())
        sent: list[str] = []
        patch_calls: list[str] = []

        async def _fake_send(target: str, text: str, attachments: Any = None) -> str:
            del target
            sent.append(text)
            return "om_001"

        async def _fake_patch(message_id: str, text: str) -> None:
            del message_id
            patch_calls.append(text)

        channel.send_message = _fake_send  # type: ignore[method-assign]
        channel._patch_message = _fake_patch  # type: ignore[method-assign]

        async def _event_stream() -> AsyncIterator[AgentEvent]:
            from eaos.agent.runner import AgentEvent

            yield AgentEvent(type="token", content="Hello")
            yield AgentEvent(type="token", content=" world")
            yield AgentEvent(type="final", content="Hello world final")

        await channel.send_streaming("oc_group1", _event_stream())

        assert sent == ["Hello"]
        assert patch_calls == ["Hello world", "Hello world final"]

    async def test_final_only_posts_once(self) -> None:
        channel = FeishuChannel(_config())
        sent: list[str] = []

        async def _fake_send(target: str, text: str, attachments: Any = None) -> str:
            del target
            sent.append(text)
            return "om_001"

        async def _fake_patch(message_id: str, text: str) -> None:
            sent.append(f"patch:{text}")

        channel.send_message = _fake_send  # type: ignore[method-assign]
        channel._patch_message = _fake_patch  # type: ignore[method-assign]

        async def _event_stream() -> AsyncIterator[AgentEvent]:
            from eaos.agent.runner import AgentEvent

            yield AgentEvent(type="final", content="just final")

        await channel.send_streaming("oc_group1", _event_stream())

        assert sent == ["just final"]


class TestVerifySignature:
    async def test_valid_signature_passes(self) -> None:
        key = "my-encrypt-key"
        channel = FeishuChannel(_config(encrypt_key=key))
        raw = b'{"event":"message"}'

        computed = hashlib.sha256(key.encode("utf-8") + raw).hexdigest()
        result = await channel.verify_signature(raw, computed)
        assert result is True

    async def test_invalid_signature_fails(self) -> None:
        channel = FeishuChannel(_config(encrypt_key="my-key"))

        result = await channel.verify_signature(b'{"event":"message"}', "wrong-sig")
        assert result is False

    async def test_no_key_skips_verification(self) -> None:
        channel = FeishuChannel(_config(encrypt_key=None))

        result = await channel.verify_signature(b'{"event":"message"}', "")
        assert result is True


class TestChannelName:
    def test_name_is_feishu(self) -> None:
        channel = FeishuChannel(_config())
        assert channel.name == "feishu"
