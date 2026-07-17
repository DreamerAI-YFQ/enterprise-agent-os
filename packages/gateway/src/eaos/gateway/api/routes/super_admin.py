"""Super admin multi-tenant management routes.

Cross-tenant CRUD for tenants, quotas, and stats. Requires the super_admin
role (separate from tenant-scoped admin).

  - GET    /super/tenants                   — list all tenants
  - POST   /super/tenants                   — create a new tenant
  - GET    /super/tenants/{tenant_id}       — tenant details
  - PATCH  /super/tenants/{tenant_id}       — update name/status/settings
  - DELETE /super/tenants/{tenant_id}       — delete tenant (cascade)
  - POST   /super/tenants/{tenant_id}/enable  — set status=active
  - POST   /super/tenants/{tenant_id}/disable — set status=suspended
  - GET    /super/tenants/{tenant_id}/quotas  — list quotas
  - PUT    /super/tenants/{tenant_id}/quotas  — replace quotas
  - GET    /super/tenants/{tenant_id}/stats   — user/skill/session counts
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002 — runtime for FastAPI
from eaos.gateway.api.deps import get_db
from eaos.infra.db.base import DbClient  # noqa: TC002 — runtime for FastAPI
from fastapi import APIRouter, Depends, HTTPException, Request  # noqa: TC002
from pydantic import BaseModel

router = APIRouter(prefix="/super")


# -- Auth dependency ---------------------------------------------------------


# Lazy import to avoid circular dependency at module load.
async def get_principal_dep(
    request: Request,
) -> Principal:
    from eaos.gateway.api.deps import get_principal

    return await get_principal(request)


async def require_super_admin(
    principal: Principal = Depends(get_principal_dep),  # noqa: B008
) -> Principal:
    """Reject non-super-admin principals with 403."""
    if principal.role != "super_admin":
        raise HTTPException(status_code=403, detail="super_admin role required")
    return principal


# -- Models ------------------------------------------------------------------


class TenantCreateRequest(BaseModel):
    name: str
    slug: str
    settings: dict[str, Any] = {}
    plan: str = "standard"


class TenantUpdateRequest(BaseModel):
    name: str | None = None
    status: str | None = None
    settings: dict[str, Any] | None = None
    plan: str | None = None


class QuotaEntry(BaseModel):
    scope: str  # "company" | "department" | "personal"
    period: str  # "monthly" | "daily"
    token_limit: int
    cost_limit_usd: float | None = None


class QuotaUpdateRequest(BaseModel):
    quotas: list[QuotaEntry]


# -- Routes ------------------------------------------------------------------


@router.get("/tenants", tags=["super"])
async def list_tenants(
    principal: Principal = Depends(require_super_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List all tenants with stats."""
    _ = principal
    rows = await db.fetch(
        "SELECT id, name, slug, status, settings, created_at, updated_at "
        "FROM iam.tenants ORDER BY created_at"
    )
    result: list[dict[str, Any]] = []
    for r in rows or []:
        settings = r.get("settings")
        if isinstance(settings, str):
            settings = json.loads(settings)
        result.append(
            {
                "id": str(r["id"]),
                "name": r["name"],
                "slug": r["slug"],
                "status": r["status"],
                "plan": (settings or {}).get("plan", "standard"),
                "settings": settings or {},
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
            }
        )
    return result


@router.post("/tenants", tags=["super"], status_code=201)
async def create_tenant(
    body: TenantCreateRequest,
    principal: Principal = Depends(require_super_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Create a new tenant."""
    _ = principal
    # Check slug uniqueness.
    existing = await db.fetch_val(
        "SELECT count(*) FROM iam.tenants WHERE slug = :p0",
        body.slug,
    )
    if existing and int(existing) > 0:
        raise HTTPException(status_code=409, detail=f"slug '{body.slug}' already exists")

    settings = {**body.settings, "plan": body.plan}
    row = await db.fetch_one(
        "INSERT INTO iam.tenants (name, slug, status, settings) "
        "VALUES (:p0, :p1, 'active', CAST(:p2 AS jsonb)) "
        "RETURNING id, name, slug, status, settings, created_at",
        body.name,
        body.slug,
        json.dumps(settings),
    )
    if row is None:
        raise HTTPException(status_code=500, detail="failed to create tenant")
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "slug": row["slug"],
        "status": row["status"],
        "settings": json.loads(row["settings"])
        if isinstance(row["settings"], str)
        else row["settings"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


@router.get("/tenants/{tenant_id}", tags=["super"])
async def get_tenant(
    tenant_id: UUID,
    principal: Principal = Depends(require_super_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Get tenant details."""
    _ = principal
    row = await db.fetch_one(
        "SELECT id, name, slug, status, settings, created_at, updated_at "
        "FROM iam.tenants WHERE id = :p0",
        tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    settings = row.get("settings")
    if isinstance(settings, str):
        settings = json.loads(settings)
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "slug": row["slug"],
        "status": row["status"],
        "plan": (settings or {}).get("plan", "standard"),
        "settings": settings or {},
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@router.patch("/tenants/{tenant_id}", tags=["super"])
async def update_tenant(
    tenant_id: UUID,
    body: TenantUpdateRequest,
    principal: Principal = Depends(require_super_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Update tenant name/status/settings/plan."""
    _ = principal
    existing = await db.fetch_one(
        "SELECT settings FROM iam.tenants WHERE id = :p0",
        tenant_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="tenant not found")

    settings = existing.get("settings") or {}
    if isinstance(settings, str):
        settings = json.loads(settings)
    if body.settings is not None:
        settings.update(body.settings)
    if body.plan is not None:
        settings["plan"] = body.plan

    sets: list[str] = []
    params: list[Any] = [tenant_id]
    if body.name is not None:
        sets.append("name = :p" + str(len(params)))
        params.append(body.name)
    if body.status is not None:
        valid = {"active", "suspended", "deleted"}
        if body.status not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"invalid status: {body.status}. Valid: {', '.join(sorted(valid))}",
            )
        sets.append("status = :p" + str(len(params)))
        params.append(body.status)
    sets.append("settings = CAST(:p" + str(len(params)) + " AS jsonb)")
    params.append(json.dumps(settings))

    await db.execute(
        f"UPDATE iam.tenants SET {', '.join(sets)}, updated_at = now() WHERE id = :p0",
        *params,
    )
    return {"id": str(tenant_id), "updated": True}


@router.delete("/tenants/{tenant_id}", tags=["super"], status_code=204)
async def delete_tenant(
    tenant_id: UUID,
    principal: Principal = Depends(require_super_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    """Delete a tenant (cascade deletes all tenant data)."""
    _ = principal
    deleted = await db.fetch_one(
        "DELETE FROM iam.tenants WHERE id = :p0 RETURNING id",
        tenant_id,
    )
    if deleted is None:
        raise HTTPException(status_code=404, detail="tenant not found")


@router.post("/tenants/{tenant_id}/enable", tags=["super"])
async def enable_tenant(
    tenant_id: UUID,
    principal: Principal = Depends(require_super_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Set tenant status to active."""
    _ = principal
    updated = await db.fetch_one(
        "UPDATE iam.tenants SET status = 'active', updated_at = now() WHERE id = :p0 RETURNING id",
        tenant_id,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return {"id": str(tenant_id), "status": "active"}


@router.post("/tenants/{tenant_id}/disable", tags=["super"])
async def disable_tenant(
    tenant_id: UUID,
    principal: Principal = Depends(require_super_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Set tenant status to suspended."""
    _ = principal
    updated = await db.fetch_one(
        "UPDATE iam.tenants SET status = 'suspended', updated_at = now() "
        "WHERE id = :p0 RETURNING id",
        tenant_id,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return {"id": str(tenant_id), "status": "suspended"}


@router.get("/tenants/{tenant_id}/quotas", tags=["super"])
async def get_tenant_quotas(
    tenant_id: UUID,
    principal: Principal = Depends(require_super_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List quotas for a tenant."""
    _ = principal
    rows = await db.fetch(
        "SELECT id, scope, owner_id, period, token_limit, token_used, "
        "cost_limit_usd, cost_used_usd, reset_at "
        "FROM harness.quotas WHERE tenant_id = :p0 ORDER BY scope, period",
        tenant_id,
    )
    return [
        {
            "id": str(r["id"]),
            "scope": r["scope"],
            "owner_id": str(r["owner_id"]) if r.get("owner_id") else None,
            "period": r["period"],
            "token_limit": r["token_limit"],
            "token_used": r["token_used"],
            "cost_limit_usd": float(r["cost_limit_usd"]) if r.get("cost_limit_usd") else None,
            "cost_used_usd": float(r["cost_used_usd"]) if r.get("cost_used_usd") else 0,
            "reset_at": r["reset_at"].isoformat() if r.get("reset_at") else None,
        }
        for r in rows or []
    ]


@router.put("/tenants/{tenant_id}/quotas", tags=["super"])
async def replace_tenant_quotas(
    tenant_id: UUID,
    body: QuotaUpdateRequest,
    principal: Principal = Depends(require_super_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Replace company-level quotas for a tenant (upsert)."""
    _ = principal
    # Verify tenant exists.
    exists = await db.fetch_val(
        "SELECT count(*) FROM iam.tenants WHERE id = :p0",
        tenant_id,
    )
    if not exists or int(exists) == 0:
        raise HTTPException(status_code=404, detail="tenant not found")

    # Delete existing company-scope quotas, then insert new ones.
    await db.execute(
        "DELETE FROM harness.quotas WHERE tenant_id = :p0 AND scope = 'company'",
        tenant_id,
    )
    from datetime import UTC, datetime, timedelta

    for q in body.quotas:
        reset = datetime.now(UTC) + timedelta(days=30 if q.period == "monthly" else 1)
        await db.execute(
            "INSERT INTO harness.quotas "
            "(tenant_id, scope, owner_id, period, token_limit, token_used, "
            "cost_limit_usd, cost_used_usd, reset_at) "
            "VALUES (:p0, 'company', NULL, :p1, :p2, 0, :p3, 0, :p4)",
            tenant_id,
            q.period,
            q.token_limit,
            q.cost_limit_usd,
            reset,
        )
    return {"tenant_id": str(tenant_id), "quotas_set": len(body.quotas)}


@router.get("/tenants/{tenant_id}/stats", tags=["super"])
async def get_tenant_stats(
    tenant_id: UUID,
    principal: Principal = Depends(require_super_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Get usage stats for a tenant (user/skill/session/agent counts)."""
    _ = principal
    stats: dict[str, int] = {}
    queries = [
        ("users", "SELECT count(*) FROM iam.users WHERE tenant_id = :p0"),
        ("departments", "SELECT count(*) FROM iam.departments WHERE tenant_id = :p0"),
        ("agents", "SELECT count(*) FROM agent.agents WHERE tenant_id = :p0"),
        ("sessions", "SELECT count(*) FROM agent.sessions WHERE tenant_id = :p0"),
        ("skills", "SELECT count(*) FROM skills.skills WHERE tenant_id = :p0"),
        ("documents", "SELECT count(*) FROM knowledge.documents WHERE tenant_id = :p0"),
        ("memories", "SELECT count(*) FROM knowledge.org_memories WHERE tenant_id = :p0"),
    ]
    for key, sql in queries:
        val = await db.fetch_val(sql, tenant_id)
        stats[key] = int(val) if val else 0
    return {"tenant_id": str(tenant_id), "stats": stats}
