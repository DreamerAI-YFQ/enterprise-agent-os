"""Verify gateway Protocols match Phase 0 contract."""

from __future__ import annotations

import dataclasses

from eaos.gateway.im.channels.base import Channel
from eaos.gateway.im.gateway import MessageGateway
from eaos.gateway.im.message import Attachment, UnifiedMessage
from eaos.gateway.multimodal.processor import (
    MultimodalProcessor,
    ProcessedInput,
    RawInput,
)
from eaos.gateway.notifier import Notifier


class TestMessage:
    def test_attachment_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(Attachment)}
        assert {"type", "url", "name"} <= fields

    def test_unifiedmessage_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(UnifiedMessage)}
        assert {
            "channel",
            "channel_message_id",
            "tenant_id",
            "user_id",
            "user_name",
            "agent_id",
            "text",
            "attachments",
            "is_mention",
            "thread_id",
            "raw",
        } <= fields


class TestChannel:
    def test_methods(self) -> None:
        for method in (
            "parse_webhook",
            "send_message",
            "send_streaming",
            "verify_signature",
        ):
            assert hasattr(Channel, method)


class TestMessageGateway:
    def test_methods(self) -> None:
        assert hasattr(MessageGateway, "handle_webhook")


class TestMultimodal:
    def test_rawinput_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(RawInput)}
        assert {"text", "attachments"} <= fields

    def test_processedinput_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(ProcessedInput)}
        assert {"parts"} <= fields

    def test_processor_methods(self) -> None:
        assert hasattr(MultimodalProcessor, "process")


class TestNotifier:
    def test_methods(self) -> None:
        for method in ("notify", "broadcast_department"):
            assert hasattr(Notifier, method)
