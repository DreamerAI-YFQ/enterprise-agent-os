"""SSE streaming invoke and HITL interrupt resume routes.

POST /invoke — streams AgentEvents as SSE data lines. Auto-creates a session
(if no session_id supplied), persists the user prompt and the agent's final
response to ``agent.messages``, and returns the session id via the
``X-Session-Id`` response header so the frontend can track the conversation.

POST /interrupt/{session_id}/resume — resumes a paused high-risk skill and
persists the resumed assistant response.

Exceptions inside the stream are converted to error events so the client
always receives a well-formed SSE response terminated by [DONE].
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.agent.runner import AgentEvent  # noqa: TC002 — runtime for error events
from eaos.core.auth import Principal  # noqa: TC002
from eaos.core.context import TenantContext
from eaos.core.errors import PermissionDeniedError
from eaos.gateway.api.deps import get_db, get_principal, get_runner
from eaos.gateway.api.routes.multimodal_loader import load_attachment
from eaos.infra.llm.base import Attachment  # noqa: TC002
from fastapi import APIRouter, Depends, HTTPException, Request  # noqa: TC002
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.runner import AgentRunner
    from eaos.infra.db.base import DbClient

logger = logging.getLogger(__name__)

router = APIRouter()


class AttachmentRef(BaseModel):
    """Reference to an uploaded file (returned by POST /upload)."""

    file_id: str
    url: str  # /uploads/{tenant}/{file_id}.{ext}
    type: str  # image | file
    name: str
    mime_type: str


class InvokeRequest(BaseModel):
    """Request body for POST /invoke."""

    agent_id: UUID
    message: str
    session_id: UUID | None = None
    attachments: list[AttachmentRef] = []


class ResumeRequest(BaseModel):
    """Request body for POST /interrupt/{session_id}/resume."""

    agent_id: UUID
    approval_id: UUID
    decision: str  # "approved" | "rejected"
    reason: str | None = None


def _serialize_event(event: AgentEvent) -> str:
    """Serialize an AgentEvent to JSON string for SSE."""
    return json.dumps(asdict(event), default=str)


def _error_event(exc: Exception) -> str:
    """Format an exception as an SSE error event."""
    return _serialize_event(AgentEvent(type="error", content=str(exc)))


def _truncate_title(message: str, max_len: int = 80) -> str:
    """Derive a session title from the first user message."""
    clean = message.strip().replace("\n", " ")
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1] + "…"


# -- Persistence helpers ------------------------------------------------------


async def _resolve_or_create_session(
    db: DbClient,
    *,
    session_id: UUID | None,
    agent_id: UUID,
    principal: Principal,
    title: str,
) -> UUID:
    """Return the session id, creating a new session row if none supplied.

    When ``session_id`` is provided, verifies tenant ownership (admins may
    access any session in their tenant; employees only their own). When
    ``None``, inserts a new row and returns its id.
    """
    if session_id is not None:
        if principal.role == "admin":
            row = await db.fetch_one(
                "SELECT id FROM agent.sessions "
                "WHERE id = :p0 AND tenant_id = :p1",
                session_id,
                principal.tenant_id,
            )
        else:
            row = await db.fetch_one(
                "SELECT id FROM agent.sessions "
                "WHERE id = :p0 AND tenant_id = :p1 AND user_id = :p2",
                session_id,
                principal.tenant_id,
                principal.user_id,
            )
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session_id

    new_id = uuid4()
    await db.execute(
        "INSERT INTO agent.sessions "
        "(id, agent_id, tenant_id, thread_id, user_id, title, status) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, 'active')",
        new_id,
        agent_id,
        principal.tenant_id,
        str(new_id),
        principal.user_id,
        title,
    )
    return new_id


async def _persist_message(
    db: DbClient,
    *,
    session_id: UUID,
    tenant_id: UUID,
    role: str,
    content: str,
    event_type: str | None = None,
) -> None:
    """Insert a row into agent.messages."""
    await db.execute(
        "INSERT INTO agent.messages "
        "(session_id, tenant_id, role, content, event_type) "
        "VALUES (:p0, :p1, :p2, :p3, :p4)",
        session_id,
        tenant_id,
        role,
        content,
        event_type,
    )


async def _touch_session(db: DbClient, session_id: UUID) -> None:
    """Bump last_active_at on the session row."""
    await db.execute(
        "UPDATE agent.sessions SET last_active_at = now() WHERE id = :p0",
        session_id,
    )


# -- Routes -------------------------------------------------------------------


@router.post("/invoke")
async def invoke(
    body: InvokeRequest,
    request: Request,
    principal: Principal = Depends(get_principal),  # noqa: B008
    runner: AgentRunner = Depends(get_runner),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> StreamingResponse:
    """Stream agent execution events as SSE.

    Persists the user message before streaming and the agent's final response
    after streaming completes. Returns ``X-Session-Id`` so the frontend can
    track the conversation.

    ``body.attachments`` carries optional multimodal references (returned by
    POST /upload); each is loaded from disk into an ``Attachment`` with either
    a base64 data URL (images) or extracted text (PDF/text files).
    """
    session_id = await _resolve_or_create_session(
        db,
        session_id=body.session_id,
        agent_id=body.agent_id,
        principal=principal,
        title=_truncate_title(body.message),
    )
    await _persist_message(
        db,
        session_id=session_id,
        tenant_id=principal.tenant_id,
        role="user",
        content=body.message,
    )
    await _touch_session(db, session_id)

    ctx = TenantContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        agent_id=body.agent_id,
        agent_scope="personal",
        session_id=session_id,
    )

    # Load attachments (image -> data URL; file -> extracted text).
    attachments: list[Attachment] = []
    if body.attachments:
        uploads_dir = getattr(request.app.state.config, "uploads", None)
        base_dir = uploads_dir.dir if uploads_dir is not None else "uploads"
        for ref in body.attachments:
            try:
                att = await load_attachment(
                    ref.url,
                    mime_type=ref.mime_type,
                    name=ref.name,
                    base_dir=base_dir,
                )
                attachments.append(att)
            except FileNotFoundError:
                logger.warning(
                    "attachment not found on disk, skipping: file_id=%s url=%s",
                    ref.file_id,
                    ref.url,
                )

    async def event_stream() -> AsyncIterator[str]:
        final_content: str | None = None
        try:
            stream = runner.invoke(
                ctx,
                body.message,
                attachments=attachments or None,
            )
            async for event in stream:
                if event.type == "final" and event.content:
                    final_content = event.content
                yield f"data: {_serialize_event(event)}\n\n"
        except PermissionDeniedError as exc:
            yield f"data: {_error_event(exc)}\n\n"
        # Best-effort: persist the assistant's final response.
        if final_content:
            try:
                await _persist_message(
                    db,
                    session_id=session_id,
                    tenant_id=principal.tenant_id,
                    role="assistant",
                    content=final_content,
                    event_type="final",
                )
                await _touch_session(db, session_id)
            except Exception:  # noqa: BLE001 — never break the stream
                logger.exception("failed to persist assistant message")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id": str(session_id),
        },
    )


@router.post("/interrupt/{session_id}/resume")
async def interrupt_resume(
    session_id: UUID,
    body: ResumeRequest,
    principal: Principal = Depends(get_principal),  # noqa: B008
    runner: AgentRunner = Depends(get_runner),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> StreamingResponse:
    """Resume a paused high-risk skill after HITL approval.

    Persists the resumed assistant response (best-effort).
    """
    ctx = TenantContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        agent_id=body.agent_id,
        agent_scope="personal",
        session_id=session_id,
    )
    approval: dict[str, Any] = {
        "id": str(body.approval_id),
        "status": body.decision,
        "reason": body.reason,
    }

    async def event_stream() -> AsyncIterator[str]:
        final_content: str | None = None
        try:
            stream = runner.interrupt_and_resume(ctx, approval)
            async for event in stream:
                if event.type == "final" and event.content:
                    final_content = event.content
                yield f"data: {_serialize_event(event)}\n\n"
        except PermissionDeniedError as exc:
            yield f"data: {_error_event(exc)}\n\n"
        if final_content:
            try:
                await _persist_message(
                    db,
                    session_id=session_id,
                    tenant_id=principal.tenant_id,
                    role="assistant",
                    content=final_content,
                    event_type="final",
                )
                await _touch_session(db, session_id)
            except Exception:  # noqa: BLE001 — never break the stream
                logger.exception("failed to persist resumed assistant message")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id": str(session_id),
        },
    )
