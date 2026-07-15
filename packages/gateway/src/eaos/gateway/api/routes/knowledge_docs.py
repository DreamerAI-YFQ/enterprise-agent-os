"""Knowledge document management API — ingest, list, get, delete.

Admin routes (``/admin/knowledge/documents``):
- ``POST`` — ingest a document (chunk + embed + store)
- ``GET`` — list documents for the tenant
- ``GET /{id}`` — get a single document
- ``DELETE /{id}`` — delete a document and its chunks

Ingestion delegates to ``RAGPipeline.ingest``; listing and deletion query the
DB directly because the pipeline exposes no list/get methods. Deletion removes
chunks first (the pipeline's delete only drops the document row).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db, get_rag_pipeline
from eaos.gateway.api.routes.admin import require_admin
from eaos.knowledge.rag.pipeline import Document, RAGPipeline  # noqa: TC002
from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: TC002
from pydantic import BaseModel

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(prefix="/admin/knowledge/documents", tags=["knowledge"])


# -- Request models -----------------------------------------------------------


class DocumentCreate(BaseModel):
    """Request body for POST — ingest a document."""

    source_type: str  # pdf/word/confluence/email/web
    source_uri: str
    title: str
    content: str
    metadata: dict[str, Any] = {}
    version: int = 1
    scope: str = "enterprise"  # personal/department/enterprise
    owner_id: str | None = None  # user_id (personal) or dept_id (department)


# -- Serializers --------------------------------------------------------------


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _doc_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "source_type": row["source_type"],
        "source_uri": row["source_uri"],
        "title": row["title"],
        "content_hash": row.get("content_hash"),
        "version": row.get("version", 1),
        "metadata": row.get("metadata", {}),
        "status": row.get("status", "indexed"),
        "scope": row.get("scope", "enterprise"),
        "owner_id": str(row["owner_id"]) if row.get("owner_id") else None,
        "created_at": _iso(row.get("created_at")),
    }


# -- Routes -------------------------------------------------------------------


@router.get("", status_code=200)
async def list_documents(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    scope: str | None = Query(None, description="Filter by scope"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List documents for the tenant (admin only)."""
    if scope:
        rows = await db.fetch(
            "SELECT id, tenant_id, source_type, source_uri, title, "
            "content_hash, version, metadata, status, scope, owner_id, created_at "
            "FROM knowledge.documents WHERE tenant_id = :p0 AND scope = :p1 "
            "ORDER BY created_at DESC LIMIT :p2 OFFSET :p3",
            principal.tenant_id,
            scope,
            limit,
            offset,
        )
    else:
        rows = await db.fetch(
            "SELECT id, tenant_id, source_type, source_uri, title, "
            "content_hash, version, metadata, status, scope, owner_id, created_at "
            "FROM knowledge.documents WHERE tenant_id = :p0 "
            "ORDER BY created_at DESC LIMIT :p1 OFFSET :p2",
            principal.tenant_id,
            limit,
            offset,
        )
    return [_doc_to_dict(r) for r in rows]


@router.get("/{document_id}", status_code=200)
async def get_document(
    document_id: str,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Get a single document (admin only)."""
    row = await db.fetch_one(
        "SELECT id, tenant_id, source_type, source_uri, title, "
        "content_hash, version, metadata, status, scope, owner_id, created_at "
        "FROM knowledge.documents WHERE id = :p0 AND tenant_id = :p1",
        document_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    return _doc_to_dict(row)


@router.get("/{document_id}/chunks", status_code=200)
async def list_document_chunks(
    document_id: str,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List chunks for a document (admin only). Embedding vector is excluded."""
    row = await db.fetch_one(
        "SELECT id FROM knowledge.documents WHERE id = :p0 AND tenant_id = :p1",
        document_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    rows = await db.fetch(
        "SELECT id, document_id, tenant_id, chunk_index, content, "
        "token_count, metadata, created_at "
        "FROM knowledge.chunks WHERE document_id = :p0 AND tenant_id = :p1 "
        "ORDER BY chunk_index ASC LIMIT :p2 OFFSET :p3",
        document_id,
        principal.tenant_id,
        limit,
        offset,
    )
    return [_chunk_to_dict(r) for r in rows]


def _chunk_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "document_id": str(row["document_id"]),
        "tenant_id": str(row["tenant_id"]),
        "chunk_index": row["chunk_index"],
        "content": row["content"],
        "token_count": row["token_count"],
        "metadata": row.get("metadata", {}),
        "created_at": _iso(row.get("created_at")),
    }


@router.post("", status_code=201)
async def ingest_document(
    body: DocumentCreate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    rag: RAGPipeline = Depends(get_rag_pipeline),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Ingest a document: chunk, embed, and store (admin only)."""
    parsed_owner_id: UUID | None = None
    if body.owner_id:
        try:
            parsed_owner_id = UUID(body.owner_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="invalid owner_id (must be UUID)"
            ) from exc

    doc = Document(
        source_type=body.source_type,
        source_uri=body.source_uri,
        title=body.title,
        content=body.content,
        metadata=body.metadata,
        version=body.version,
        scope=body.scope,
        owner_id=parsed_owner_id,
    )
    chunk_ids = await rag.ingest(doc, principal.tenant_id)
    row = await db.fetch_one(
        "SELECT id, tenant_id, source_type, source_uri, title, "
        "content_hash, version, metadata, status, scope, owner_id, created_at "
        "FROM knowledge.documents "
        "WHERE tenant_id = :p0 AND title = :p1 AND source_uri = :p2 "
        "ORDER BY created_at DESC LIMIT 1",
        principal.tenant_id,
        body.title,
        body.source_uri,
    )
    if row is None:
        return {"chunk_ids": [str(c) for c in chunk_ids], "chunk_count": len(chunk_ids)}
    result = _doc_to_dict(row)
    result["chunk_ids"] = [str(c) for c in chunk_ids]
    result["chunk_count"] = len(chunk_ids)
    return result


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    """Delete a document and its chunks (admin only)."""
    row = await db.fetch_one(
        "SELECT id FROM knowledge.documents WHERE id = :p0 AND tenant_id = :p1",
        document_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    await db.execute(
        "DELETE FROM knowledge.chunks WHERE document_id = :p0",
        document_id,
    )
    await db.execute(
        "DELETE FROM knowledge.documents WHERE id = :p0 AND tenant_id = :p1",
        document_id,
        principal.tenant_id,
    )
