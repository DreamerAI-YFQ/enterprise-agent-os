"""Session API — conversation history for the employee chat UI.

``GET /sessions`` lists the current user's recent conversations.
``GET /sessions/{id}`` returns session metadata. ``GET /sessions/{id}/messages``
returns the ordered message history. ``DELETE /sessions/{id}`` removes a
session and its messages (FK cascades). ``PATCH /sessions/{id}`` renames it.

All routes enforce tenant + user ownership: a session belonging to another
user (even within the same tenant) returns 404.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db, get_principal
from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: TC002
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(prefix="/sessions", tags=["sessions"])


# -- Request / response models ------------------------------------------------


class SessionUpdate(BaseModel):
    """Request body for PATCH /sessions/{id} — rename or close a session."""

    title: str | None = None
    status: str | None = None


# -- Serializers --------------------------------------------------------------


def _session_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "agent_id": str(row["agent_id"]),
        "tenant_id": str(row["tenant_id"]),
        "user_id": str(row["user_id"]),
        "title": row.get("title"),
        "status": row.get("status", "active"),
        "created_at": _iso(row.get("created_at")),
        "last_active_at": _iso(row.get("last_active_at")),
    }


def _message_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "session_id": str(row["session_id"]),
        "role": row["role"],
        "content": row["content"],
        "event_type": row.get("event_type"),
        "created_at": _iso(row.get("created_at")),
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# -- Helpers ------------------------------------------------------------------


async def _fetch_owned_session(
    db: DbClient, session_id: UUID, principal: Principal
) -> dict[str, Any]:
    """Fetch a session row, enforcing tenant + user ownership.

    Admins may access any session in their tenant; employees only their own.
    Returns the row or raises 404 (ownership failure is reported as 404 to
    avoid leaking the existence of other users' sessions).
    """
    if principal.role == "admin":
        row = await db.fetch_one(
            "SELECT id, agent_id, tenant_id, user_id, title, status, "
            "created_at, last_active_at "
            "FROM agent.sessions "
            "WHERE id = :p0 AND tenant_id = :p1",
            session_id,
            principal.tenant_id,
        )
    else:
        row = await db.fetch_one(
            "SELECT id, agent_id, tenant_id, user_id, title, status, "
            "created_at, last_active_at "
            "FROM agent.sessions "
            "WHERE id = :p0 AND tenant_id = :p1 AND user_id = :p2",
            session_id,
            principal.tenant_id,
            principal.user_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return row


# -- Routes -------------------------------------------------------------------


@router.get("", status_code=200)
async def list_sessions(
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    q: str | None = Query(None, description="Search session titles"),
    limit: int = Query(50, ge=1, le=200, description="Max sessions to return"),
) -> list[dict[str, Any]]:
    """List the current user's recent sessions, newest first.

    Admins see all sessions in their tenant; employees see only their own.
    If ``q`` is provided, filters by title (case-insensitive ILIKE).
    """
    params: list[Any] = [principal.tenant_id]
    where_clauses: list[str] = ["tenant_id = :p0"]
    idx = 1

    if principal.role != "admin":
        where_clauses.append(f"user_id = :p{idx}")
        params.append(principal.user_id)
        idx += 1

    if q:
        where_clauses.append(f"title ILIKE :p{idx}")
        params.append(f"%{q}%")
        idx += 1

    where_sql = " AND ".join(where_clauses)
    params.append(limit)

    sql = (
        "SELECT id, agent_id, tenant_id, user_id, title, status, "
        "created_at, last_active_at "
        "FROM agent.sessions "
        f"WHERE {where_sql} "
        f"ORDER BY last_active_at DESC LIMIT :p{idx}"
    )
    rows = await db.fetch(sql, *params)
    return [_session_to_dict(r) for r in rows]


@router.get("/{session_id}", status_code=200)
async def get_session(
    session_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Get a single session (ownership enforced)."""
    row = await _fetch_owned_session(db, session_id, principal)
    return _session_to_dict(row)


@router.get("/{session_id}/messages", status_code=200)
async def list_messages(
    session_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    limit: int = Query(100, ge=1, le=500, description="Max messages to return"),
) -> list[dict[str, Any]]:
    """List messages for a session, oldest first (chat order)."""
    await _fetch_owned_session(db, session_id, principal)
    rows = await db.fetch(
        "SELECT id, session_id, tenant_id, role, content, event_type, created_at "
        "FROM agent.messages "
        "WHERE session_id = :p0 AND tenant_id = :p1 "
        "ORDER BY created_at ASC LIMIT :p2",
        session_id,
        principal.tenant_id,
        limit,
    )
    return [_message_to_dict(r) for r in rows]


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    """Delete a session and its messages (ownership enforced)."""
    await _fetch_owned_session(db, session_id, principal)
    await db.execute(
        "DELETE FROM agent.sessions WHERE id = :p0 AND tenant_id = :p1",
        session_id,
        principal.tenant_id,
    )


@router.patch("/{session_id}", status_code=200)
async def update_session(
    session_id: UUID,
    body: SessionUpdate,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Update a session's title and/or status (ownership enforced)."""
    await _fetch_owned_session(db, session_id, principal)
    if body.title is not None:
        await db.execute(
            "UPDATE agent.sessions SET title = :p0 "
            "WHERE id = :p1 AND tenant_id = :p2",
            body.title,
            session_id,
            principal.tenant_id,
        )
    if body.status is not None:
        await db.execute(
            "UPDATE agent.sessions SET status = :p0 "
            "WHERE id = :p1 AND tenant_id = :p2",
            body.status,
            session_id,
            principal.tenant_id,
        )
    row = await _fetch_owned_session(db, session_id, principal)
    return _session_to_dict(row)


@router.get("/{session_id}/export", status_code=200, response_class=PlainTextResponse)
async def export_session(
    session_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> str:
    """Export a session as Markdown (ownership enforced)."""
    session = await _fetch_owned_session(db, session_id, principal)
    rows = await db.fetch(
        "SELECT id, session_id, tenant_id, role, content, event_type, created_at "
        "FROM agent.messages "
        "WHERE session_id = :p0 AND tenant_id = :p1 "
        "ORDER BY created_at ASC",
        session_id,
        principal.tenant_id,
    )
    title = session.get("title") or "未命名对话"
    created = _iso(session.get("created_at")) or ""
    lines = [f"# {title}", "", f"**创建时间:** {created}", "", "---", ""]
    for row in rows:
        role = row["role"]
        content = row.get("content", "")
        ts = _iso(row.get("created_at")) or ""
        role_label = {"user": "🧑 用户", "assistant": "🤖 助手", "system": "⚙️ 系统"}.get(
            role, role
        )
        lines.append(f"### {role_label}")
        lines.append(f"*{ts}*")
        lines.append("")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


@router.post("/{session_id}/title/auto", status_code=200)
async def auto_title_session(
    session_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    """Auto-generate a title from the first user message (ownership enforced).

    Truncates the first user message to 30 characters. If no user message
    exists, falls back to "新对话".
    """
    await _fetch_owned_session(db, session_id, principal)
    row = await db.fetch_one(
        "SELECT content FROM agent.messages "
        "WHERE session_id = :p0 AND tenant_id = :p1 AND role = 'user' "
        "ORDER BY created_at ASC LIMIT 1",
        session_id,
        principal.tenant_id,
    )
    if row is None or not row.get("content"):
        title = "新对话"
    else:
        content = row["content"].strip().replace("\n", " ")
        title = content[:30] + ("..." if len(content) > 30 else "")

    await db.execute(
        "UPDATE agent.sessions SET title = :p0 "
        "WHERE id = :p1 AND tenant_id = :p2",
        title,
        session_id,
        principal.tenant_id,
    )
    return {"id": str(session_id), "title": title}
