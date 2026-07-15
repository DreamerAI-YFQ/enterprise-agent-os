"""Notification API — user notification inbox.

``GET /notifications`` lists the current user's notifications (newest first).
``PUT /notifications/{id}/read`` marks a single notification as read.
``PUT /notifications/read-all`` marks all unread notifications as read.

Notifications are persisted in ``iam.notifications`` (migration 0011). They are
written by ambient triggers, approval status changes, and system alerts.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db, get_principal
from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: TC002

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _notification_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "type": row.get("type", "system"),
        "title": row.get("title", ""),
        "body": row.get("body"),
        "read": row.get("read_at") is not None,
        "related_entity_type": row.get("related_entity_type"),
        "related_entity_id": str(row["related_entity_id"])
        if row.get("related_entity_id")
        else None,
        "created_at": _iso(row.get("created_at")),
        "read_at": _iso(row.get("read_at")),
    }


async def _fetch_owned_notification(
    db: DbClient, notification_id: UUID, principal: Principal
) -> dict[str, Any]:
    """Fetch a notification, enforcing tenant + user ownership."""
    row = await db.fetch_one(
        "SELECT id, tenant_id, user_id, type, title, body, "
        "related_entity_type, related_entity_id, read_at, created_at "
        "FROM iam.notifications "
        "WHERE id = :p0 AND tenant_id = :p1 AND user_id = :p2",
        notification_id,
        principal.tenant_id,
        principal.user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    return row


@router.get("", status_code=200)
async def list_notifications(
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    unread_only: bool = Query(False, description="Return only unread notifications"),
    limit: int = Query(50, ge=1, le=200, description="Max notifications to return"),
) -> list[dict[str, Any]]:
    """List the current user's notifications, newest first."""
    if unread_only:
        rows = await db.fetch(
            "SELECT id, tenant_id, user_id, type, title, body, "
            "related_entity_type, related_entity_id, read_at, created_at "
            "FROM iam.notifications "
            "WHERE tenant_id = :p0 AND user_id = :p1 AND read_at IS NULL "
            "ORDER BY created_at DESC LIMIT :p2",
            principal.tenant_id,
            principal.user_id,
            limit,
        )
    else:
        rows = await db.fetch(
            "SELECT id, tenant_id, user_id, type, title, body, "
            "related_entity_type, related_entity_id, read_at, created_at "
            "FROM iam.notifications "
            "WHERE tenant_id = :p0 AND user_id = :p1 "
            "ORDER BY created_at DESC LIMIT :p2",
            principal.tenant_id,
            principal.user_id,
            limit,
        )
    return [_notification_to_dict(r) for r in rows]


@router.get("/unread-count", status_code=200)
async def unread_count(
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, int]:
    """Return the count of unread notifications for the current user."""
    row = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM iam.notifications "
        "WHERE tenant_id = :p0 AND user_id = :p1 AND read_at IS NULL",
        principal.tenant_id,
        principal.user_id,
    )
    return {"unread_count": int(row["cnt"]) if row else 0}


@router.put("/{notification_id}/read", status_code=200)
async def mark_read(
    notification_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Mark a single notification as read."""
    await _fetch_owned_notification(db, notification_id, principal)
    await db.execute(
        "UPDATE iam.notifications SET read_at = now() "
        "WHERE id = :p0 AND tenant_id = :p1 AND user_id = :p2",
        notification_id,
        principal.tenant_id,
        principal.user_id,
    )
    row = await _fetch_owned_notification(db, notification_id, principal)
    return _notification_to_dict(row)


@router.put("/read-all", status_code=200)
async def mark_all_read(
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, int]:
    """Mark all unread notifications as read for the current user."""
    count_row = await db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM iam.notifications "
        "WHERE tenant_id = :p0 AND user_id = :p1 AND read_at IS NULL",
        principal.tenant_id,
        principal.user_id,
    )
    marked = int(count_row["cnt"]) if count_row else 0
    await db.execute(
        "UPDATE iam.notifications SET read_at = now() "
        "WHERE tenant_id = :p0 AND user_id = :p1 AND read_at IS NULL",
        principal.tenant_id,
        principal.user_id,
    )
    return {"marked": marked}
