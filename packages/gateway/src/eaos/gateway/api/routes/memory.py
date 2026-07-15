"""Memory API — three-tier (personal/department/enterprise) memory management.

Employee routes (``/memory``):
- ``GET /memory`` — list all visible memories (personal + department + enterprise)
  Optional ``scope`` filter narrows to one tier. Optional ``q`` does semantic recall.
- ``POST /memory`` — create a memory (employees can only create personal)
- ``DELETE /memory/{id}`` — delete a memory (owner only; admin can delete any)

Admin routes (``/admin/memory``):
- ``GET /admin/memory`` — list all tenant memories with optional scope filter
- ``POST /admin/memory/{id}/promote`` — promote memory scope (personal→dept→enterprise)

Memories are stored in ``knowledge.org_memories`` with scope/owner_id columns.
``MemoryEngine`` handles semantic recall; listing/promotion query the DB directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db, get_memory_engine, get_principal
from eaos.gateway.api.routes.admin import require_admin
from eaos.infra.db.base import DbClient  # noqa: TC002
from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: TC002
from pydantic import BaseModel

if TYPE_CHECKING:
    from eaos.agent.memory.engine import MemoryEngine

router = APIRouter(tags=["memory"])


# -- Helpers ------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _memory_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "scope": row.get("scope", "personal"),
        "owner_id": str(row["owner_id"]) if row.get("owner_id") else None,
        "memory_type": row.get("memory_type", "fact"),
        "content": row.get("content", ""),
        "confidence": float(row.get("confidence", 0.5)),
        "source": row.get("source", "agent"),
        "created_at": _iso(row.get("created_at")),
        "last_accessed": _iso(row.get("last_accessed")),
        "access_count": row.get("access_count", 0),
    }


# -- Request models -----------------------------------------------------------


class MemoryCreate(BaseModel):
    content: str
    memory_type: str = "fact"
    scope: str = "personal"  # employees can only create personal
    owner_id: str | None = None  # department_id when scope=department (admin only)


class PromoteRequest(BaseModel):
    new_scope: str  # "department" or "enterprise"
    new_owner_id: str | None = None  # department_id when promoting to department


class MemoryUpdate(BaseModel):
    content: str | None = None
    memory_type: str | None = None
    confidence: float | None = None


class BatchDeleteRequest(BaseModel):
    memory_ids: list[UUID]


class BatchPromoteRequest(BaseModel):
    memory_ids: list[UUID]
    new_scope: str  # "department" or "enterprise"
    new_owner_id: str | None = None  # department_id when promoting to department


# -- Employee routes ----------------------------------------------------------


@router.get("/memory", status_code=200)
async def list_memories(
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    engine: MemoryEngine = Depends(get_memory_engine),  # noqa: B008
    q: str | None = Query(None, description="Semantic query (uses recall)"),
    scope: str | None = Query(None, description="Filter by scope"),
    limit: int = Query(50, ge=1, le=200, description="Max memories to return"),
) -> list[dict[str, Any]]:
    """List all memories visible to the user (personal + department + enterprise).

    If ``q`` is provided, delegates to semantic recall instead of listing.
    If ``scope`` is provided, narrows to that scope only.
    """
    # Semantic recall path
    if q is not None:
        from eaos.knowledge.memory.store import MemoryScope

        target_scope = MemoryScope(scope) if scope else MemoryScope.PERSONAL
        owner = principal.user_id if target_scope == MemoryScope.PERSONAL else None
        results = await engine.recall(
            q,
            principal.tenant_id,
            scope=target_scope,
            owner_id=owner,
            top_k=limit,
        )
        return [
            {
                "id": str(m.id),
                "scope": str(m.scope.value) if hasattr(m.scope, "value") else str(m.scope),
                "owner_id": str(m.owner_id) if m.owner_id else None,
                "memory_type": str(m.memory_type.value)
                if hasattr(m.memory_type, "value")
                else str(m.memory_type),
                "content": m.content,
                "confidence": m.confidence,
                "source": m.source,
                "created_at": _iso(m.created_at),
                "last_accessed": _iso(m.last_accessed),
                "access_count": m.access_count,
            }
            for m in results
        ]

    # List path: build visibility query
    if scope == "personal":
        where = (
            "WHERE tenant_id = :p0 AND scope = 'personal' AND owner_id = :p1"
        )
        rows = await db.fetch(
            f"SELECT id, tenant_id, scope, owner_id, memory_type, content, "
            f"confidence, source, created_at, last_accessed, access_count "
            f"FROM knowledge.org_memories {where} "
            f"ORDER BY created_at DESC LIMIT :p2",
            principal.tenant_id,
            principal.user_id,
            limit,
        )
    elif scope == "department":
        rows = await db.fetch(
            "SELECT m.id, m.tenant_id, m.scope, m.owner_id, m.memory_type, "
            "m.content, m.confidence, m.source, m.created_at, m.last_accessed, "
            "m.access_count FROM knowledge.org_memories m "
            "WHERE m.tenant_id = :p0 AND m.scope = 'department' "
            "AND m.owner_id IN ("
            "  SELECT department_id FROM iam.memberships WHERE user_id = :p1"
            ") ORDER BY m.created_at DESC LIMIT :p2",
            principal.tenant_id,
            principal.user_id,
            limit,
        )
    elif scope == "enterprise":
        rows = await db.fetch(
            "SELECT id, tenant_id, scope, owner_id, memory_type, content, "
            "confidence, source, created_at, last_accessed, access_count "
            "FROM knowledge.org_memories "
            "WHERE tenant_id = :p0 AND scope = 'enterprise' "
            "ORDER BY created_at DESC LIMIT :p1",
            principal.tenant_id,
            limit,
        )
    else:
        # All visible: personal + department (via memberships) + enterprise
        rows = await db.fetch(
            "SELECT id, tenant_id, scope, owner_id, memory_type, content, "
            "confidence, source, created_at, last_accessed, access_count "
            "FROM knowledge.org_memories "
            "WHERE tenant_id = :p0 AND ("
            "  (scope = 'personal' AND owner_id = :p1) OR "
            "  (scope = 'department' AND owner_id IN ("
            "    SELECT department_id FROM iam.memberships WHERE user_id = :p1"
            "  )) OR "
            "  (scope = 'enterprise')"
            ") ORDER BY created_at DESC LIMIT :p2",
            principal.tenant_id,
            principal.user_id,
            limit,
        )
    return [_memory_to_dict(r) for r in rows]


@router.post("/memory", status_code=201)
async def create_memory(
    body: MemoryCreate,
    principal: Principal = Depends(get_principal),  # noqa: B008
    engine: MemoryEngine = Depends(get_memory_engine),  # noqa: B008
) -> dict[str, str]:
    """Create a memory. Employees can only create personal-scope memories.

    Admins can create department/enterprise memories by passing scope + owner_id.
    """
    from eaos.knowledge.memory.store import MemoryScope

    scope = MemoryScope(body.scope) if body.scope else MemoryScope.PERSONAL

    # Non-admins can only create personal memories
    if scope != MemoryScope.PERSONAL and principal.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="only admins can create department/enterprise memories",
        )

    owner_id: UUID | None
    if scope == MemoryScope.PERSONAL:
        owner_id = principal.user_id
    elif scope == MemoryScope.DEPARTMENT:
        if body.owner_id is None:
            raise HTTPException(
                status_code=422,
                detail="department scope requires owner_id (department id)",
            )
        owner_id = UUID(body.owner_id)
    else:  # ENTERPRISE
        owner_id = None

    memory_id = await engine.store(
        content=body.content,
        tenant_id=principal.tenant_id,
        scope=scope,
        owner_id=owner_id,
        memory_type=body.memory_type,
        source="manual",
    )
    return {"id": str(memory_id)}


@router.delete("/memory/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: UUID,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    """Delete a memory. Owner can delete own; admin can delete any."""
    if principal.role == "admin":
        row = await db.fetch_one(
            "SELECT id FROM knowledge.org_memories "
            "WHERE id = :p0 AND tenant_id = :p1",
            memory_id,
            principal.tenant_id,
        )
    else:
        row = await db.fetch_one(
            "SELECT id FROM knowledge.org_memories "
            "WHERE id = :p0 AND tenant_id = :p1 AND owner_id = :p2",
            memory_id,
            principal.tenant_id,
            principal.user_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="memory not found")
    await db.execute(
        "DELETE FROM knowledge.org_memories WHERE id = :p0 AND tenant_id = :p1",
        memory_id,
        principal.tenant_id,
    )


@router.patch("/memory/{memory_id}", status_code=200)
async def update_memory(
    memory_id: UUID,
    body: MemoryUpdate,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    engine: MemoryEngine = Depends(get_memory_engine),  # noqa: B008
) -> dict[str, str]:
    """Edit a memory's content/type/confidence.

    Employees can only edit their own personal memories; admins can edit any.
    Recomputes the embedding when content changes.
    """
    if principal.role == "admin":
        row = await db.fetch_one(
            "SELECT id, scope, owner_id FROM knowledge.org_memories "
            "WHERE id = :p0 AND tenant_id = :p1",
            memory_id,
            principal.tenant_id,
        )
    else:
        row = await db.fetch_one(
            "SELECT id, scope, owner_id FROM knowledge.org_memories "
            "WHERE id = :p0 AND tenant_id = :p1 AND scope = 'personal' "
            "AND owner_id = :p2",
            memory_id,
            principal.tenant_id,
            principal.user_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="memory not found")
    if body.content is None and body.memory_type is None and body.confidence is None:
        raise HTTPException(status_code=422, detail="no fields to update")
    await engine.update(
        memory_id,
        principal.tenant_id,
        content=body.content,
        memory_type=body.memory_type,
        confidence=body.confidence,
    )
    return {"id": str(memory_id)}


# -- Admin routes -------------------------------------------------------------


@router.get("/admin/memory", status_code=200)
async def admin_list_memories(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    engine: MemoryEngine = Depends(get_memory_engine),  # noqa: B008
    q: str | None = Query(None, description="Semantic query (uses recall)"),
    scope: str | None = Query(None, description="Filter by scope"),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """List all memories in the tenant (admin only).

    If ``q`` is provided, delegates to semantic recall across all scopes.
    """
    if q is not None:
        from eaos.knowledge.memory.store import MemoryScope

        per_scope = max(1, limit // 3)
        all_results: list[Any] = []
        for mem_scope in (
            MemoryScope.PERSONAL,
            MemoryScope.DEPARTMENT,
            MemoryScope.ENTERPRISE,
        ):
            all_results.extend(
                await engine.recall(
                    q,
                    principal.tenant_id,
                    scope=mem_scope,
                    owner_id=None,
                    top_k=per_scope,
                )
            )
        all_results.sort(key=lambda m: m.confidence, reverse=True)
        all_results = all_results[:limit]
        return [
            {
                "id": str(m.id),
                "scope": str(m.scope.value) if hasattr(m.scope, "value") else str(m.scope),
                "owner_id": str(m.owner_id) if m.owner_id else None,
                "memory_type": str(m.memory_type.value)
                if hasattr(m.memory_type, "value")
                else str(m.memory_type),
                "content": m.content,
                "confidence": m.confidence,
                "source": m.source,
                "created_at": _iso(m.created_at),
                "last_accessed": _iso(m.last_accessed),
                "access_count": m.access_count,
            }
            for m in all_results
        ]

    if scope:
        rows = await db.fetch(
            "SELECT id, tenant_id, scope, owner_id, memory_type, content, "
            "confidence, source, created_at, last_accessed, access_count "
            "FROM knowledge.org_memories "
            "WHERE tenant_id = :p0 AND scope = :p1 "
            "ORDER BY created_at DESC LIMIT :p2",
            principal.tenant_id,
            scope,
            limit,
        )
    else:
        rows = await db.fetch(
            "SELECT id, tenant_id, scope, owner_id, memory_type, content, "
            "confidence, source, created_at, last_accessed, access_count "
            "FROM knowledge.org_memories "
            "WHERE tenant_id = :p0 "
            "ORDER BY created_at DESC LIMIT :p1",
            principal.tenant_id,
            limit,
        )
    return [_memory_to_dict(r) for r in rows]


@router.post("/admin/memory/{memory_id}/promote", status_code=200)
async def promote_memory(
    memory_id: UUID,
    body: PromoteRequest,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    """Promote a memory to a higher scope (admin only).

    personal → department (requires new_owner_id = department id)
    department → enterprise (new_owner_id = None)
    """
    from eaos.knowledge.memory.store import MemoryScope

    new_scope = MemoryScope(body.new_scope)
    new_owner_id: UUID | None = None
    if new_scope == MemoryScope.DEPARTMENT:
        if body.new_owner_id is None:
            raise HTTPException(
                status_code=422,
                detail="department scope requires new_owner_id",
            )
        new_owner_id = UUID(body.new_owner_id)

    row = await db.fetch_one(
        "SELECT id FROM knowledge.org_memories "
        "WHERE id = :p0 AND tenant_id = :p1",
        memory_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="memory not found")

    await db.execute(
        "UPDATE knowledge.org_memories "
        "SET scope = :p0, owner_id = :p1 "
        "WHERE id = :p2 AND tenant_id = :p3",
        str(new_scope.value),
        new_owner_id,
        memory_id,
        principal.tenant_id,
    )
    return {"id": str(memory_id), "scope": str(new_scope.value)}


@router.post("/admin/memory/batch-delete", status_code=200)
async def batch_delete_memories(
    body: BatchDeleteRequest,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, int]:
    """Batch-delete memories by IDs (admin only)."""
    if not body.memory_ids:
        return {"deleted": 0}
    placeholders = ", ".join(f":p{i}" for i in range(len(body.memory_ids)))
    await db.execute(
        f"DELETE FROM knowledge.org_memories "
        f"WHERE tenant_id = :p{len(body.memory_ids)} AND id IN ({placeholders})",
        *body.memory_ids,
        principal.tenant_id,
    )
    return {"deleted": len(body.memory_ids)}


@router.post("/admin/memory/batch-promote", status_code=200)
async def batch_promote_memories(
    body: BatchPromoteRequest,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Batch-promote memory scope (admin only).

    personal → department (requires new_owner_id = department id)
    department → enterprise (new_owner_id = None)
    """
    from eaos.knowledge.memory.store import MemoryScope

    new_scope = MemoryScope(body.new_scope)
    new_owner_id: UUID | None = None
    if new_scope == MemoryScope.DEPARTMENT:
        if body.new_owner_id is None:
            raise HTTPException(
                status_code=422,
                detail="department scope requires new_owner_id",
            )
        new_owner_id = UUID(body.new_owner_id)

    if not body.memory_ids:
        return {"promoted": 0}

    placeholders = ", ".join(f":p{i+3}" for i in range(len(body.memory_ids)))
    await db.execute(
        f"UPDATE knowledge.org_memories "
        f"SET scope = :p0, owner_id = :p1 "
        f"WHERE tenant_id = :p2 AND id IN ({placeholders})",
        str(new_scope.value),
        new_owner_id,
        principal.tenant_id,
        *body.memory_ids,
    )
    return {"promoted": len(body.memory_ids), "scope": str(new_scope.value)}
