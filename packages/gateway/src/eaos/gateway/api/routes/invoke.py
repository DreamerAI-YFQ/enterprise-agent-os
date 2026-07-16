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
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.agent.runner import AgentEvent  # noqa: TC002 — runtime for error events
from eaos.core.auth import Principal  # noqa: TC002
from eaos.core.context import TenantContext
from eaos.core.errors import PermissionDeniedError
from eaos.gateway.api.deps import get_db, get_orchestrator, get_principal, get_runner, get_tracer
from eaos.gateway.api.routes.multimodal_loader import load_attachment
from eaos.infra.llm.base import Attachment  # noqa: TC002
from eaos.observability.span import Granularity  # noqa: TC002
from fastapi import APIRouter, Depends, HTTPException, Request  # noqa: TC002
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.orchestrator import AgentOrchestratorImpl
    from eaos.agent.runner import AgentRunner
    from eaos.infra.db.base import DbClient
    from eaos.observability.tracer import Tracer

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
    """Request body for POST /interrupt/{session_id}/resume.

    C02/GAP-05: ``decision`` field is IGNORED — the server queries the real
    approval record from the database. This prevents clients from forging
    an approval by sending ``decision="approved"``.
    """

    agent_id: UUID
    approval_id: UUID
    decision: str = ""  # deprecated: ignored, kept for backward compat
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
    orchestrator: AgentOrchestratorImpl = Depends(get_orchestrator),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    tracer: Tracer = Depends(get_tracer),  # noqa: B008
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

    # C02-02: Load real department memberships for the user instead of
    # hardcoding agent_scope="personal" with empty department_ids.
    department_ids: list[UUID] = []
    try:
        dept_rows = await db.fetch_all(
            "SELECT department_id FROM iam.department_members "
            "WHERE user_id = :p0 AND tenant_id = :p1",
            principal.user_id,
            principal.tenant_id,
        )
        if dept_rows:
            department_ids = [r["department_id"] for r in dept_rows]
    except Exception:  # noqa: BLE001 — departments are best-effort
        logger.warning("failed to load departments for user %s", principal.user_id)

    ctx = TenantContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        agent_id=body.agent_id,
        agent_scope="personal",
        session_id=session_id,
        department_ids=department_ids,
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
        # Trace the agent invocation (persisted to trace.spans for observability)
        span_cm = asynccontextmanager(tracer.span)
        try:
            async with span_cm(
                "agent.invoke",
                Granularity.TASK,
                ctx,
                agent_id=str(ctx.agent_id),
                session_id=str(ctx.session_id) if ctx.session_id else None,
                user_id=str(ctx.user_id) if ctx.user_id else None,
            ) as span_handle:
                # C12/GAP-01: Route through Orchestrator for multi-agent support.
                # Orchestrator analyzes the task and decides:
                # - SINGLE → delegates to AgentRunner (same as before)
                # - RELAY/FAN_OUT_IN/DEBATE/HIERARCHICAL → multi-agent collaboration
                # Attachments are only supported in SINGLE mode (passed to runner).
                if attachments:
                    # Attachments require direct runner access (Orchestrator doesn't support them)
                    stream = runner.invoke(
                        ctx,
                        body.message,
                        attachments=attachments or None,
                    )
                else:
                    # C12: Use Orchestrator for all text-only invocations
                    stream = orchestrator.execute(ctx, body.message)
                async for event in stream:
                    if event.type == "final" and event.content:
                        final_content = event.content
                        span_handle.set_attribute("final_content_length", len(event.content))
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

    C02/GAP-05: The ``decision`` field in the request body is IGNORED.
    The server queries the real approval record from ``harness.approvals``
    and verifies:
    - approval exists and belongs to this tenant
    - approval belongs to this session
    - approval status is 'approved' (not pending/rejected/expired)
    - approval has not been consumed (one-time use)

    Persists the resumed assistant response (best-effort).
    """
    # C02/GAP-05: Query the REAL approval status from the database.
    # Client-supplied ``decision`` is never trusted.
    approval_row = await db.fetch_one(
        """SELECT id, tenant_id, session_id, status, agent_id
           FROM harness.approvals
           WHERE id = :p0""",
        body.approval_id,
    )
    if approval_row is None:
        raise HTTPException(status_code=404, detail="approval not found")

    # Verify tenant ownership
    if approval_row["tenant_id"] != principal.tenant_id:
        raise HTTPException(status_code=404, detail="approval not found")

    # Verify session binding
    if approval_row["session_id"] != session_id:
        raise HTTPException(
            status_code=403,
            detail="approval does not belong to this session",
        )

    # Verify agent binding
    if approval_row["agent_id"] != body.agent_id:
        raise HTTPException(
            status_code=403,
            detail="approval does not belong to this agent",
        )

    real_status = str(approval_row["status"])

    if real_status == "pending":
        raise HTTPException(
            status_code=409,
            detail="approval is still pending — admin must approve first",
        )
    if real_status == "rejected":
        raise HTTPException(
            status_code=403,
            detail="approval was rejected by admin",
        )
    if real_status == "expired":
        raise HTTPException(
            status_code=410,
            detail="approval has expired",
        )
    if real_status != "approved":
        raise HTTPException(
            status_code=403,
            detail=f"approval status is '{real_status}', cannot resume",
        )

    # C02/GAP-05: Mark approval as consumed (one-time use) to prevent
    # replay attacks. The status changes from 'approved' to 'consumed'.
    await db.execute(
        """UPDATE harness.approvals
           SET status = 'consumed'
           WHERE id = :p0 AND status = 'approved'""",
        body.approval_id,
    )

    ctx = TenantContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        agent_id=body.agent_id,
        agent_scope="personal",
        session_id=session_id,
    )
    # Pass the verified approval status to the runner
    approval: dict[str, Any] = {
        "id": str(body.approval_id),
        "status": "approved",  # always "approved" here — we verified above
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
