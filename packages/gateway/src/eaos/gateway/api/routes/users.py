"""Admin user management API — CRUD on iam.users.

- ``GET /admin/users`` — list users in the tenant
- ``POST /admin/users`` — create a user
- ``GET /admin/users/{id}`` — get a single user
- ``PUT /admin/users/{id}`` — update name/role/status
- ``DELETE /admin/users/{id}`` — delete a user
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003

from eaos.core.auth import Principal, hash_password  # noqa: TC002
from eaos.gateway.api.deps import get_db
from eaos.gateway.api.routes.admin import require_admin
from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: TC002
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(prefix="/admin/users", tags=["users"])


class UserCreate(BaseModel):
    email: str
    name: str
    password: str = Field(min_length=8, max_length=1024)
    role: str = "employee"
    status: str = "active"


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    status: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=1024)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _user_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "status": row.get("status", "active"),
        "created_at": _iso(row.get("created_at")),
    }


@router.get("", status_code=200)
async def list_users(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    role: str | None = Query(None),  # noqa: B008
    limit: int = Query(50, ge=1, le=200),  # noqa: B008
) -> list[dict[str, Any]]:
    """List users in the tenant (admin only)."""
    if role:
        rows = await db.fetch(
            "SELECT id, tenant_id, email, name, role, status, created_at "
            "FROM iam.users WHERE tenant_id = :p0 AND role = :p1 "
            "ORDER BY created_at DESC LIMIT :p2",
            principal.tenant_id,
            role,
            limit,
        )
    else:
        rows = await db.fetch(
            "SELECT id, tenant_id, email, name, role, status, created_at "
            "FROM iam.users WHERE tenant_id = :p0 "
            "ORDER BY created_at DESC LIMIT :p1",
            principal.tenant_id,
            limit,
        )
    return [_user_to_dict(r) for r in rows]


@router.post("", status_code=201)
async def create_user(
    body: UserCreate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Create a user in the tenant (admin only)."""
    existing = await db.fetch_one(
        "SELECT id FROM iam.users WHERE tenant_id = :p0 AND email = :p1",
        principal.tenant_id,
        body.email,
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="email already exists")
    password_hash = await run_in_threadpool(hash_password, body.password)
    row = await db.fetch_one(
        "INSERT INTO iam.users "
        "(tenant_id, email, name, password_hash, role, status) "
        "VALUES (:p0, :p1, :p2, :p3, :p4, :p5) "
        "RETURNING id, tenant_id, email, name, role, status, created_at",
        principal.tenant_id,
        body.email,
        body.name,
        password_hash,
        body.role,
        body.status,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="failed to create user")
    return _user_to_dict(row)


@router.get("/{user_id}", status_code=200)
async def get_user(
    user_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Get a single user (admin only)."""
    row = await db.fetch_one(
        "SELECT id, tenant_id, email, name, role, status, created_at "
        "FROM iam.users WHERE id = :p0 AND tenant_id = :p1",
        user_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    return _user_to_dict(row)


@router.put("/{user_id}", status_code=200)
async def update_user(
    user_id: UUID,
    body: UserUpdate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Update a user's profile and optionally reset the local password."""
    existing = await db.fetch_one(
        "SELECT id FROM iam.users WHERE id = :p0 AND tenant_id = :p1",
        user_id,
        principal.tenant_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="user not found")
    if body.name is not None:
        await db.execute(
            "UPDATE iam.users SET name = :p0 WHERE id = :p1 AND tenant_id = :p2",
            body.name,
            user_id,
            principal.tenant_id,
        )
    if body.role is not None:
        await db.execute(
            "UPDATE iam.users SET role = :p0 WHERE id = :p1 AND tenant_id = :p2",
            body.role,
            user_id,
            principal.tenant_id,
        )
    if body.status is not None:
        await db.execute(
            "UPDATE iam.users SET status = :p0 WHERE id = :p1 AND tenant_id = :p2",
            body.status,
            user_id,
            principal.tenant_id,
        )
    if body.password is not None:
        password_hash = await run_in_threadpool(hash_password, body.password)
        await db.execute(
            "UPDATE iam.users SET password_hash = :p0 WHERE id = :p1 AND tenant_id = :p2",
            password_hash,
            user_id,
            principal.tenant_id,
        )
    row = await db.fetch_one(
        "SELECT id, tenant_id, email, name, role, status, created_at "
        "FROM iam.users WHERE id = :p0 AND tenant_id = :p1",
        user_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="user not found after update")
    return _user_to_dict(row)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    """Delete a user (admin only)."""
    existing = await db.fetch_one(
        "SELECT id FROM iam.users WHERE id = :p0 AND tenant_id = :p1",
        user_id,
        principal.tenant_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="user not found")
    await db.execute(
        "DELETE FROM iam.users WHERE id = :p0 AND tenant_id = :p1",
        user_id,
        principal.tenant_id,
    )


@router.get("/{user_id}/departments", status_code=200)
async def list_user_departments(
    user_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List departments a user belongs to (admin only)."""
    rows = await db.fetch(
        "SELECT d.id, d.name, m.role as member_role, m.joined_at "
        "FROM iam.memberships m "
        "JOIN iam.departments d ON d.id = m.department_id "
        "WHERE m.user_id = :p0 AND d.tenant_id = :p1 "
        "ORDER BY d.name",
        user_id,
        principal.tenant_id,
    )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "member_role": r.get("member_role", "member"),
            "joined_at": _iso(r.get("joined_at")),
        }
        for r in rows
    ]
