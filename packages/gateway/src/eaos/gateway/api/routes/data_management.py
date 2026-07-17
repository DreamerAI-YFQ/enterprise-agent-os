"""Admin data export/import — bulk portability for users, memory, knowledge, sessions.

- ``GET /admin/export/{resource}?format=json|csv`` — export tenant data as a download
- ``POST /admin/import/{resource}`` — import records from a JSON body

All routes require the admin role. Exports are tenant-scoped. Imports create
records with new UUIDs to avoid collisions; existing records (matched by email
for users, content hash for memory, title+content for knowledge) are skipped
or updated based on the ``mode`` parameter (``skip`` default, ``upsert``).
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db
from eaos.gateway.api.routes.admin import require_admin
from eaos.infra.db.base import DbClient  # noqa: TC002
from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: TC002
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/admin", tags=["admin/data-management"])

_VALID_RESOURCES = {"users", "memory", "knowledge", "sessions"}
_IMPORTABLE = {"users", "memory", "knowledge"}


# -- Schema maps -------------------------------------------------------------


def _columns(resource: str) -> tuple[str, str, list[str]]:
    """Return (table, order_by, columns) for a resource."""
    if resource == "users":
        return (
            "iam.users",
            "created_at",
            [
                "id",
                "email",
                "name",
                "role",
                "status",
                "preferences",
                "created_at",
            ],
        )
    if resource == "memory":
        return (
            "knowledge.org_memories",
            "created_at",
            [
                "id",
                "scope",
                "owner_id",
                "memory_type",
                "content",
                "confidence",
                "source",
                "created_at",
            ],
        )
    if resource == "knowledge":
        # knowledge.documents has no content column — content lives in chunks.
        # Export pulls documents with concatenated chunk content via subquery.
        return (
            "knowledge.documents",
            "created_at",
            [
                "id",
                "title",
                "source_type",
                "source_uri",
                "status",
                "metadata",
                "created_at",
            ],
        )
    # sessions
    return (
        "agent.sessions",
        "created_at",
        [
            "id",
            "agent_id",
            "user_id",
            "title",
            "status",
            "created_at",
            "last_active_at",
        ],
    )


def _serialize(value: Any) -> Any:
    """Make a DB value JSON-safe."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "hex"):  # UUID
        return str(value)
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _row_to_dict(row: Any, columns: list[str]) -> dict[str, Any]:
    return {col: _serialize(row[col]) for col in columns}


# -- Export ------------------------------------------------------------------


@router.get("/export/{resource}", status_code=200)
async def export_resource(
    resource: str,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    format: str = Query(default="json", pattern="^(json|csv)$"),  # noqa: B008
) -> StreamingResponse:
    """Export tenant data for the given resource as JSON or CSV."""
    if resource not in _VALID_RESOURCES:
        raise HTTPException(status_code=404, detail=f"unknown resource: {resource}")

    table, order_by, columns = _columns(resource)

    if resource == "knowledge":
        # Special-case: pull documents with concatenated chunk content.
        rows = await db.fetch(
            "SELECT d.id, d.title, d.source_type, d.source_uri, d.status, "
            "d.metadata, d.created_at, "
            "COALESCE(string_agg(c.content, E'\n' ORDER BY c.chunk_index), '') AS content "
            "FROM knowledge.documents d "
            "LEFT JOIN knowledge.chunks c ON c.document_id = d.id "
            "WHERE d.tenant_id = :p0 "
            "GROUP BY d.id, d.title, d.source_type, d.source_uri, d.status, "
            "d.metadata, d.created_at "
            "ORDER BY d.created_at LIMIT 10000",
            principal.tenant_id,
        )
        # Use the same columns plus content for serialization.
        export_cols = columns + ["content"]
        items = [_row_to_dict(r, export_cols) for r in rows or []]
    else:
        rows = await db.fetch(
            f"SELECT {', '.join(columns)} FROM {table} "
            f"WHERE tenant_id = :p0 ORDER BY {order_by} LIMIT 10000",
            principal.tenant_id,
        )
        items = [_row_to_dict(r, columns) for r in rows or []]

    if format == "csv":
        # Use whatever columns the items actually have.
        csv_cols = list(items[0].keys()) if items else columns
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(csv_cols)
        for item in items:
            writer.writerow(
                [
                    json.dumps(item[col], ensure_ascii=False)
                    if isinstance(item.get(col), (dict, list))
                    else item.get(col, "")
                    for col in csv_cols
                ]
            )
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{resource}.csv"',
            },
        )

    # JSON
    payload = json.dumps(
        {"resource": resource, "tenant_id": str(principal.tenant_id), "items": items},
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{resource}.json"',
        },
    )


# -- Import ------------------------------------------------------------------


class ImportRequest(BaseModel):
    """Import payload — items array matching the resource schema."""

    items: list[dict[str, Any]]
    mode: str = "skip"  # skip | upsert


@router.post("/import/{resource}", status_code=200)
async def import_resource(
    resource: str,
    body: ImportRequest,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Import records for the given resource (users/memory/knowledge only)."""
    if resource not in _IMPORTABLE:
        raise HTTPException(status_code=404, detail=f"resource not importable: {resource}")
    if body.mode not in {"skip", "upsert"}:
        raise HTTPException(status_code=422, detail="mode must be skip or upsert")

    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    for idx, item in enumerate(body.items):
        try:
            if resource == "users":
                c, u, s = await _import_user(db, principal.tenant_id, item, body.mode)
            elif resource == "memory":
                c, u, s = await _import_memory(db, principal.tenant_id, item, body.mode)
            else:  # knowledge
                c, u, s = await _import_knowledge(db, principal.tenant_id, item, body.mode)
            created += c
            updated += u
            skipped += s
        except Exception as exc:  # noqa: BLE001
            errors.append(f"row {idx}: {exc}")

    return {
        "resource": resource,
        "total": len(body.items),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


async def _import_user(
    db: DbClient, tenant_id: Any, item: dict[str, Any], mode: str
) -> tuple[int, int, int]:
    email = item.get("email")
    if not email:
        raise ValueError("email is required")
    existing = await db.fetch_one(
        "SELECT id FROM iam.users WHERE tenant_id = :p0 AND email = :p1",
        tenant_id,
        email,
    )
    if existing:
        if mode == "upsert":
            await db.execute(
                "UPDATE iam.users SET name = :p0, role = :p1, status = :p2 "
                "WHERE id = :p3 AND tenant_id = :p4",
                item.get("name", email),
                item.get("role", "employee"),
                item.get("status", "active"),
                existing["id"],
                tenant_id,
            )
            return 0, 1, 0
        return 0, 0, 1
    await db.execute(
        "INSERT INTO iam.users (tenant_id, email, name, role, status) "
        "VALUES (:p0, :p1, :p2, :p3, :p4)",
        tenant_id,
        email,
        item.get("name", email),
        item.get("role", "employee"),
        item.get("status", "active"),
    )
    return 1, 0, 0


async def _import_memory(
    db: DbClient, tenant_id: Any, item: dict[str, Any], mode: str
) -> tuple[int, int, int]:
    content = item.get("content")
    if not content:
        raise ValueError("content is required")
    scope = item.get("scope", "personal")
    owner_email = item.get("owner_email") or item.get("owner_id")
    owner_id: Any = None
    if owner_email:
        row = await db.fetch_one(
            "SELECT id FROM iam.users WHERE tenant_id = :p0 AND (email = :p1 OR id::text = :p2)",
            tenant_id,
            str(owner_email),
            str(owner_email),
        )
        owner_id = row["id"] if row else None
    if scope == "personal" and owner_id is None:
        # fall back to enterprise scope if owner not found
        scope = "enterprise"
    existing = await db.fetch_one(
        "SELECT id FROM knowledge.org_memories "
        "WHERE tenant_id = :p0 AND content = :p1 AND scope = :p2",
        tenant_id,
        content,
        scope,
    )
    if existing:
        if mode == "upsert":
            await db.execute(
                "UPDATE knowledge.org_memories SET memory_type = :p0, confidence = :p1 "
                "WHERE id = :p2",
                item.get("memory_type", "fact"),
                float(item.get("confidence", 0.9)),
                existing["id"],
            )
            return 0, 1, 0
        return 0, 0, 1
    await db.execute(
        "INSERT INTO knowledge.org_memories "
        "(tenant_id, scope, owner_id, memory_type, content, confidence, source) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6)",
        tenant_id,
        scope,
        owner_id,
        item.get("memory_type", "fact"),
        content,
        float(item.get("confidence", 0.9)),
        item.get("source", "import"),
    )
    return 1, 0, 0


async def _import_knowledge(
    db: DbClient, tenant_id: Any, item: dict[str, Any], mode: str
) -> tuple[int, int, int]:
    title = item.get("title")
    content = item.get("content")
    if not title or not content:
        raise ValueError("title and content are required")
    import hashlib

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = await db.fetch_one(
        "SELECT id FROM knowledge.documents "
        "WHERE tenant_id = :p0 AND title = :p1 AND content_hash = :p2",
        tenant_id,
        title,
        content_hash,
    )
    if existing:
        if mode == "upsert":
            await db.execute(
                "UPDATE knowledge.documents SET source_type = :p0, status = :p1 WHERE id = :p2",
                item.get("source_type", "manual"),
                item.get("status", "approved"),
                existing["id"],
            )
            return 0, 1, 0
        return 0, 0, 1
    # Insert document + single chunk containing all content.
    doc_row = await db.fetch_one(
        "INSERT INTO knowledge.documents "
        "(tenant_id, title, source_type, source_uri, content_hash, status, metadata) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, CAST(:p6 AS jsonb)) "
        "RETURNING id",
        tenant_id,
        title,
        item.get("source_type", "manual"),
        item.get("source_uri", ""),
        content_hash,
        item.get("status", "approved"),
        json.dumps(item.get("metadata", {}), ensure_ascii=False),
    )
    if doc_row:
        # Approximate token count: 1 token ≈ 4 chars.
        token_count = max(1, len(content) // 4)
        await db.execute(
            "INSERT INTO knowledge.chunks "
            "(document_id, tenant_id, chunk_index, content, token_count, metadata) "
            "VALUES (:p0, :p1, 0, :p2, :p3, CAST('{}' AS jsonb))",
            doc_row["id"],
            tenant_id,
            content,
            token_count,
        )
    return 1, 0, 0
