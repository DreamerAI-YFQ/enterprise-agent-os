"""Task API — unified task list aggregating approvals and sessions.

``GET /tasks?status=pending|running|completed`` returns a merged view of:

- **pending**: ``harness.approvals`` rows with ``status='pending'`` for the
  current user (or all pending for admins).
- **running**: ``agent.sessions`` rows with ``status='active'`` for the user.
- **completed**: ``agent.sessions`` rows with ``status='completed'`` for the
  user.

Without a ``status`` filter, all three categories are returned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db, get_principal
from fastapi import APIRouter, Depends, Query  # noqa: TC002

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _approval_to_task(row: dict[str, Any]) -> dict[str, Any]:
    """Convert an approval row to the unified task shape."""
    return {
        "id": str(row["id"]),
        "type": "approval",
        "status": "pending",
        "title": f"Approval required: {row.get('reason', 'high_risk')}",
        "description": row.get("reason"),
        "agent_id": str(row["agent_id"]) if row.get("agent_id") else None,
        "session_id": str(row["session_id"]) if row.get("session_id") else None,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("decided_at") or row.get("created_at")),
        "related": {
            "approval_id": str(row["id"]),
            "reason": row.get("reason"),
            "requested_by": str(row["requested_by"]) if row.get("requested_by") else None,
        },
    }


def _session_to_task(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a session row to the unified task shape."""
    status = row.get("status", "active")
    task_status = "running" if status == "active" else "completed"
    return {
        "id": str(row["id"]),
        "type": "session",
        "status": task_status,
        "title": row.get("title") or "Untitled session",
        "description": None,
        "agent_id": str(row["agent_id"]) if row.get("agent_id") else None,
        "session_id": str(row["id"]),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("last_active_at")),
        "related": {
            "thread_id": row.get("thread_id"),
        },
    }


async def _fetch_pending_approvals(
    db: DbClient, principal: Principal
) -> list[dict[str, Any]]:
    """Fetch pending approvals for the user (admins see all tenant pending)."""
    if principal.role == "admin":
        rows = await db.fetch(
            "SELECT id, tenant_id, agent_id, session_id, reason, status, "
            "requested_by, decided_by, decided_at, created_at "
            "FROM harness.approvals "
            "WHERE tenant_id = :p0 AND status = 'pending' "
            "ORDER BY created_at DESC",
            principal.tenant_id,
        )
    else:
        rows = await db.fetch(
            "SELECT id, tenant_id, agent_id, session_id, reason, status, "
            "requested_by, decided_by, decided_at, created_at "
            "FROM harness.approvals "
            "WHERE tenant_id = :p0 AND status = 'pending' "
            "AND requested_by = :p1 "
            "ORDER BY created_at DESC",
            principal.tenant_id,
            principal.user_id,
        )
    return [_approval_to_task(r) for r in rows]


async def _fetch_sessions_by_status(
    db: DbClient, principal: Principal, *, active: bool
) -> list[dict[str, Any]]:
    """Fetch sessions filtered by active/completed status."""
    status_filter = "active" if active else "completed"
    if principal.role == "admin":
        rows = await db.fetch(
            "SELECT id, agent_id, tenant_id, user_id, title, status, "
            "thread_id, created_at, last_active_at "
            "FROM agent.sessions "
            "WHERE tenant_id = :p0 AND status = :p1 "
            "ORDER BY last_active_at DESC LIMIT :p2",
            principal.tenant_id,
            status_filter,
            100,
        )
    else:
        rows = await db.fetch(
            "SELECT id, agent_id, tenant_id, user_id, title, status, "
            "thread_id, created_at, last_active_at "
            "FROM agent.sessions "
            "WHERE tenant_id = :p0 AND user_id = :p1 AND status = :p2 "
            "ORDER BY last_active_at DESC LIMIT :p3",
            principal.tenant_id,
            principal.user_id,
            status_filter,
            100,
        )
    return [_session_to_task(r) for r in rows]


@router.get("", status_code=200)
async def list_tasks(
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    status: str | None = Query(
        None, description="Filter: pending | running | completed"
    ),
) -> list[dict[str, Any]]:
    """List tasks (approvals + sessions) filtered by status.

    Without a filter, returns pending approvals + running sessions + completed
    sessions, merged and sorted by ``updated_at`` descending.
    """
    if status == "pending":
        return await _fetch_pending_approvals(db, principal)
    if status == "running":
        return await _fetch_sessions_by_status(db, principal, active=True)
    if status == "completed":
        return await _fetch_sessions_by_status(db, principal, active=False)

    # No filter — merge all
    pending = await _fetch_pending_approvals(db, principal)
    running = await _fetch_sessions_by_status(db, principal, active=True)
    completed = await _fetch_sessions_by_status(db, principal, active=False)
    merged = pending + running + completed
    merged.sort(key=lambda t: t.get("updated_at") or t.get("created_at") or "", reverse=True)
    return merged
