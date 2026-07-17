"""Knowledge contribution submission + admin review workflow.

Employees submit knowledge documents (manual text, file-backed, or URL) for
admin review. Approved submissions are ingested into the indexed
``knowledge.documents`` table via the RAG pipeline; rejected submissions
retain the reviewer's comment for the submitter to see. The contribution
row remains as an audit record even after approval — the indexed document
lives separately in ``knowledge.documents``.

Endpoints:
- Employee: ``POST /knowledge/contributions``, ``GET /knowledge/contributions/mine``
- Admin:    ``GET /admin/contributions``, ``GET /admin/contributions/{id}``,
            ``POST /admin/contributions/{id}/review``

Recoverability: ``rag.ingest()`` finishes embeddings before creating a
document, records in-progress indexing, and repairs missing or partial chunks
on retry. On approve, we call ``rag.ingest()`` first; if it raises, the
contribution stays ``pending`` and the API returns 500. The subsequent UPDATE
and notification INSERT use
two ``db.execute()`` calls (matching the pattern in ``knowledge_docs.py``
delete). If the notification INSERT fails after the UPDATE, the
contribution is marked approved but the submitter is not notified —
recoverable by manual notification. Re-approve is safe (``rag.ingest``
verifies the existing chunk set before returning).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db, get_principal, get_rag_pipeline
from eaos.gateway.api.routes.admin import require_admin
from eaos.knowledge.rag.pipeline import Document, RAGPipeline  # noqa: TC002
from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: TC002
from pydantic import BaseModel

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(tags=["contributions"])


# -- Request models -----------------------------------------------------------


class ContributionCreate(BaseModel):
    """Request body for POST /knowledge/contributions — submit a contribution."""

    title: str
    content: str
    source_type: str = "manual"  # manual/url/file_upload
    source_uri: str | None = None
    metadata: dict[str, Any] = {}


class ContributionReview(BaseModel):
    """Request body for POST /admin/contributions/{id}/review."""

    decision: Literal["approved", "rejected"]
    reason: str | None = None


class ContributionUpdate(BaseModel):
    """Request body for PATCH /knowledge/contributions/{id} — edit a pending contribution."""

    title: str | None = None
    content: str | None = None
    source_type: str | None = None
    source_uri: str | None = None


class ContributionResubmit(BaseModel):
    """Revise and resubmit a rejected contribution."""

    title: str | None = None
    content: str | None = None
    source_type: str | None = None
    source_uri: str | None = None


# -- Serializers --------------------------------------------------------------


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _contrib_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "submitter_id": str(row["submitter_id"]),
        "source_type": row["source_type"],
        "source_uri": row.get("source_uri"),
        "title": row["title"],
        "content": row["content"],
        "status": row["status"],
        "reviewer_id": str(row["reviewer_id"]) if row.get("reviewer_id") else None,
        "review_comment": row.get("review_comment"),
        "submitted_at": _iso(row.get("submitted_at")),
        "reviewed_at": _iso(row.get("reviewed_at")),
        "metadata": row.get("metadata", {}),
    }


# -- Employee endpoints -------------------------------------------------------


@router.post("/knowledge/contributions", status_code=201)
async def submit_contribution(
    body: ContributionCreate,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Submit a knowledge contribution for admin review (employee + admin)."""
    await db.execute(
        "INSERT INTO knowledge.contributions "
        "(tenant_id, submitter_id, source_type, source_uri, title, content, "
        "status, metadata) VALUES "
        "(:p0, :p1, :p2, :p3, :p4, :p5, 'pending', CAST(:p6 AS jsonb))",
        principal.tenant_id,
        principal.user_id,
        body.source_type,
        body.source_uri,
        body.title,
        body.content,
        json.dumps(body.metadata),
    )
    row = await db.fetch_one(
        "SELECT id, tenant_id, submitter_id, source_type, source_uri, title, "
        "content, status, reviewer_id, review_comment, submitted_at, "
        "reviewed_at, metadata "
        "FROM knowledge.contributions "
        "WHERE tenant_id = :p0 AND submitter_id = :p1 "
        "ORDER BY submitted_at DESC LIMIT 1",
        principal.tenant_id,
        principal.user_id,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="insert failed")
    return _contrib_to_dict(row)


@router.get("/knowledge/contributions/mine", status_code=200)
async def list_my_contributions(
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List the current user's own contributions."""
    rows = await db.fetch(
        "SELECT id, tenant_id, submitter_id, source_type, source_uri, title, "
        "content, status, reviewer_id, review_comment, submitted_at, "
        "reviewed_at, metadata "
        "FROM knowledge.contributions "
        "WHERE tenant_id = :p0 AND submitter_id = :p1 "
        "ORDER BY submitted_at DESC LIMIT :p2 OFFSET :p3",
        principal.tenant_id,
        principal.user_id,
        limit,
        offset,
    )
    return [_contrib_to_dict(r) for r in rows]


@router.delete("/knowledge/contributions/{contribution_id}", status_code=204)
async def withdraw_contribution(
    contribution_id: str,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    """Withdraw (delete) a pending or rejected contribution. Only the submitter
    can delete. Approved contributions are retained as audit records and cannot
    be deleted (the content has already been ingested into the knowledge base)."""
    row = await db.fetch_one(
        "SELECT submitter_id, status FROM knowledge.contributions "
        "WHERE id = :p0 AND tenant_id = :p1",
        contribution_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="contribution not found")
    if row["submitter_id"] != principal.user_id:
        raise HTTPException(status_code=403, detail="can only delete your own contribution")
    if row["status"] == "approved":
        raise HTTPException(status_code=409, detail="approved contributions cannot be deleted")
    await db.execute(
        "DELETE FROM knowledge.contributions WHERE id = :p0 AND tenant_id = :p1",
        contribution_id,
        principal.tenant_id,
    )


@router.patch("/knowledge/contributions/{contribution_id}", status_code=200)
async def update_contribution(
    contribution_id: str,
    body: ContributionUpdate,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Edit a pending contribution's title/content/source. Only the submitter
    can edit, and only while the contribution is still pending review."""
    row = await db.fetch_one(
        "SELECT submitter_id, status FROM knowledge.contributions "
        "WHERE id = :p0 AND tenant_id = :p1",
        contribution_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="contribution not found")
    if row["submitter_id"] != principal.user_id:
        raise HTTPException(status_code=403, detail="can only edit your own contribution")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="can only edit pending contributions")

    set_parts: list[str] = []
    args: list[Any] = []
    for field, value in [
        ("title", body.title),
        ("content", body.content),
        ("source_type", body.source_type),
        ("source_uri", body.source_uri),
    ]:
        if value is not None:
            set_parts.append(f"{field} = :p{len(args)}")
            args.append(value)
    if not set_parts:
        raise HTTPException(status_code=400, detail="no fields to update")

    args.append(contribution_id)
    args.append(principal.tenant_id)
    where_id_idx = len(args) - 2
    where_tenant_idx = len(args) - 1
    await db.execute(
        f"UPDATE knowledge.contributions SET {', '.join(set_parts)} "
        f"WHERE id = :p{where_id_idx} AND tenant_id = :p{where_tenant_idx}",
        *args,
    )

    updated = await db.fetch_one(
        "SELECT id, tenant_id, submitter_id, source_type, source_uri, title, "
        "content, status, reviewer_id, review_comment, submitted_at, "
        "reviewed_at, metadata FROM knowledge.contributions "
        "WHERE id = :p0 AND tenant_id = :p1",
        contribution_id,
        principal.tenant_id,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="contribution vanished after update")
    return _contrib_to_dict(updated)


@router.post("/knowledge/contributions/{contribution_id}/resubmit", status_code=200)
async def resubmit_contribution(
    contribution_id: str,
    body: ContributionResubmit,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Revise and resubmit a rejected contribution. Resets status to pending,
    clears review fields, bumps submitted_at, and optionally updates
    title/content/source. Only the submitter can resubmit, and only if the
    contribution was rejected."""
    row = await db.fetch_one(
        "SELECT submitter_id, status FROM knowledge.contributions "
        "WHERE id = :p0 AND tenant_id = :p1",
        contribution_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="contribution not found")
    if row["submitter_id"] != principal.user_id:
        raise HTTPException(status_code=403, detail="can only resubmit your own contribution")
    if row["status"] != "rejected":
        raise HTTPException(status_code=409, detail="can only resubmit rejected contributions")

    set_parts: list[str] = [
        "status = 'pending'",
        "reviewer_id = NULL",
        "review_comment = NULL",
        "reviewed_at = NULL",
        "submitted_at = now()",
    ]
    args: list[Any] = []
    for field, value in [
        ("title", body.title),
        ("content", body.content),
        ("source_type", body.source_type),
        ("source_uri", body.source_uri),
    ]:
        if value is not None:
            set_parts.append(f"{field} = :p{len(args)}")
            args.append(value)

    args.append(contribution_id)
    args.append(principal.tenant_id)
    where_id_idx = len(args) - 2
    where_tenant_idx = len(args) - 1
    await db.execute(
        f"UPDATE knowledge.contributions SET {', '.join(set_parts)} "
        f"WHERE id = :p{where_id_idx} AND tenant_id = :p{where_tenant_idx}",
        *args,
    )

    updated = await db.fetch_one(
        "SELECT id, tenant_id, submitter_id, source_type, source_uri, title, "
        "content, status, reviewer_id, review_comment, submitted_at, "
        "reviewed_at, metadata FROM knowledge.contributions "
        "WHERE id = :p0 AND tenant_id = :p1",
        contribution_id,
        principal.tenant_id,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="contribution vanished after resubmit")
    return _contrib_to_dict(updated)


# -- Admin endpoints ----------------------------------------------------------


@router.get("/admin/contributions", status_code=200)
async def list_contributions(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List all contributions for the tenant (admin only)."""
    if status is None:
        rows = await db.fetch(
            "SELECT id, tenant_id, submitter_id, source_type, source_uri, title, "
            "content, status, reviewer_id, review_comment, submitted_at, "
            "reviewed_at, metadata "
            "FROM knowledge.contributions "
            "WHERE tenant_id = :p0 "
            "ORDER BY submitted_at DESC LIMIT :p1 OFFSET :p2",
            principal.tenant_id,
            limit,
            offset,
        )
    else:
        rows = await db.fetch(
            "SELECT id, tenant_id, submitter_id, source_type, source_uri, title, "
            "content, status, reviewer_id, review_comment, submitted_at, "
            "reviewed_at, metadata "
            "FROM knowledge.contributions "
            "WHERE tenant_id = :p0 AND status = :p1 "
            "ORDER BY submitted_at DESC LIMIT :p2 OFFSET :p3",
            principal.tenant_id,
            status,
            limit,
            offset,
        )
    return [_contrib_to_dict(r) for r in rows]


@router.get("/admin/contributions/{contribution_id}", status_code=200)
async def get_contribution(
    contribution_id: str,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Get a single contribution (admin only)."""
    row = await db.fetch_one(
        "SELECT id, tenant_id, submitter_id, source_type, source_uri, title, "
        "content, status, reviewer_id, review_comment, submitted_at, "
        "reviewed_at, metadata "
        "FROM knowledge.contributions "
        "WHERE id = :p0 AND tenant_id = :p1",
        contribution_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="contribution not found")
    return _contrib_to_dict(row)


@router.post("/admin/contributions/{contribution_id}/review", status_code=200)
async def review_contribution(
    contribution_id: str,
    body: ContributionReview,
    principal: Principal = Depends(require_admin),  # noqa: B008
    rag: RAGPipeline = Depends(get_rag_pipeline),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Approve or reject a contribution (admin only).

    On approve: ingests the content into ``knowledge.documents`` via the
    RAG pipeline, then updates the contribution row + sends a notification.
    On reject: updates the contribution row + sends a notification.
    """
    row = await db.fetch_one(
        "SELECT id, tenant_id, submitter_id, source_type, source_uri, title, "
        "content, status, metadata "
        "FROM knowledge.contributions "
        "WHERE id = :p0 AND tenant_id = :p1",
        contribution_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="contribution not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="contribution already reviewed")

    submitter_id = row["submitter_id"]
    title = row["title"]

    if body.decision == "approved":
        # Always ask the idempotent pipeline to verify the indexed chunk set.
        # A prior attempt may have created the document row but failed before
        # vector insertion; row existence alone is not proof of completion.
        contrib_metadata = row.get("metadata") or {}
        if not isinstance(contrib_metadata, dict):
            contrib_metadata = {}
        doc_metadata = {
            **contrib_metadata,
            "contribution_id": str(row["id"]),
        }
        doc = Document(
            source_type=row["source_type"],
            source_uri=row.get("source_uri") or f"contribution://{row['id']}",
            title=title,
            content=row["content"],
            metadata=doc_metadata,
        )
        await rag.ingest(doc, principal.tenant_id)

        await db.execute(
            "UPDATE knowledge.contributions "
            "SET status = 'approved', reviewer_id = :p0, "
            "review_comment = :p1, reviewed_at = now() "
            "WHERE id = :p2 AND tenant_id = :p3",
            principal.user_id,
            body.reason,
            contribution_id,
            principal.tenant_id,
        )
        await db.execute(
            "INSERT INTO iam.notifications "
            "(tenant_id, user_id, type, title, body, "
            "related_entity_type, related_entity_id) VALUES "
            "(:p0, :p1, 'contribution_review', :p2, :p3, 'contribution', :p4)",
            principal.tenant_id,
            submitter_id,
            f"知识贡献已通过：{title}",
            body.reason or "您的贡献已被管理员审核通过并加入知识库。",
            contribution_id,
        )
    else:
        await db.execute(
            "UPDATE knowledge.contributions "
            "SET status = 'rejected', reviewer_id = :p0, "
            "review_comment = :p1, reviewed_at = now() "
            "WHERE id = :p2 AND tenant_id = :p3",
            principal.user_id,
            body.reason,
            contribution_id,
            principal.tenant_id,
        )
        await db.execute(
            "INSERT INTO iam.notifications "
            "(tenant_id, user_id, type, title, body, "
            "related_entity_type, related_entity_id) VALUES "
            "(:p0, :p1, 'contribution_review', :p2, :p3, 'contribution', :p4)",
            principal.tenant_id,
            submitter_id,
            f"知识贡献未通过：{title}",
            body.reason or "您的贡献未被管理员通过，请根据反馈修改后重新提交。",
            contribution_id,
        )

    # Re-fetch the updated row.
    updated = await db.fetch_one(
        "SELECT id, tenant_id, submitter_id, source_type, source_uri, title, "
        "content, status, reviewer_id, review_comment, submitted_at, "
        "reviewed_at, metadata "
        "FROM knowledge.contributions "
        "WHERE id = :p0 AND tenant_id = :p1",
        contribution_id,
        principal.tenant_id,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="contribution vanished after review")
    return _contrib_to_dict(updated)
