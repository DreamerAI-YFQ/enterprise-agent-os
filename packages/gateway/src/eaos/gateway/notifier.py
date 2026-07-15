"""Notifier — proactive push notifications to users.

Used by ambient monitor and Harness alerts. Channels: IM (DingTalk/Slack),
web push, email (future).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID


class Notifier(Protocol):
    """Proactive notification sender."""

    async def notify(
        self,
        tenant_id: UUID,
        user_id: UUID,
        message: str,
        channel: str | None = None,
    ) -> None:
        """Send a notification to a specific user."""
        ...

    async def broadcast_department(
        self,
        tenant_id: UUID,
        dept_id: UUID,
        message: str,
    ) -> None:
        """Broadcast to all members of a department."""
        ...

    async def notify_admins(
        self,
        tenant_id: UUID,
        message: str,
    ) -> None:
        """Notify all admins of a tenant (Harness alerts, RL promotion requests)."""
        ...
