"""Unified message model — channel-agnostic representation.

Agents only handle UnifiedMessage; channel adapters (DingTalk, Slack, WeCom,
Feishu) translate between channel-specific formats and UnifiedMessage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True)
class Attachment:
    """A message attachment (image/file/voice)."""

    type: str  # image/file/voice
    url: str
    name: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class UnifiedMessage:
    """Cross-channel unified message — agents only see this."""

    channel: str  # dingtalk/wecom/slack/feishu
    channel_message_id: str
    tenant_id: UUID
    user_id: UUID  # mapped from channel user via UserMappingRepository
    user_name: str
    agent_id: UUID  # the @tagged agent
    text: str
    attachments: list[Attachment] = field(default_factory=list)
    is_mention: bool = False  # whether agent was @tagged
    thread_id: str | None = None  # group thread/conversation id
    raw: dict[str, Any] = field(default_factory=dict)  # original payload for debugging
