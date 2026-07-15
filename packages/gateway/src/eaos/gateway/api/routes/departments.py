"""Department management API — list, create, edit, delete, member management.

Employee routes (``/departments``):
- ``GET /departments`` — list all departments in the tenant (for selectors)

Admin routes (``/admin/departments``):
- ``POST /admin/departments`` — create a department
- ``PUT /admin/departments/{id}`` — rename / reparent
- ``DELETE /admin/departments/{id}`` — delete a department
- ``POST /admin/departments/{id}/members`` — add a member
- ``DELETE /admin/departments/{id}/members/{user_id}`` — remove a member
- ``GET /admin/departments/{id}`` — department detail with member list
"""

from __future__ import annotations

from typing import Any
from uuid import UUID  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db, get_principal
from eaos.gateway.api.routes.admin import require_admin
from eaos.infra.db.base import DbClient  # noqa: TC002
from fastapi import APIRouter, Depends, HTTPException  # noqa: TC002
from pydantic import BaseModel

router = APIRouter(tags=["departments"])


# -- Request models -----------------------------------------------------------


class DepartmentCreate(BaseModel):
    name: str
    parent_id: UUID | None = None


class DepartmentUpdate(BaseModel):
    name: str | None = None
    parent_id: UUID | None = None


class MemberAdd(BaseModel):
    user_id: UUID
    role: str = "member"


# -- Serializers --------------------------------------------------------------


def _dept_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "parent_id": str(row["parent_id"]) if row.get("parent_id") else None,
        "created_at": row.get("created_at"),
    }


def _member_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(row["user_id"]),
        "department_id": str(row["department_id"]),
        "role": row.get("role", "member"),
        "joined_at": row.get("joined_at"),
    }


# -- Employee routes ----------------------------------------------------------


@router.get("/departments", status_code=200)
async def list_departments(
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List all departments in the tenant (for selectors)."""
    rows = await db.fetch(
        "SELECT id, name, parent_id, created_at FROM iam.departments "
        "WHERE tenant_id = :p0 ORDER BY name",
        principal.tenant_id,
    )
    return [_dept_to_dict(r) for r in rows]


# -- Admin routes -------------------------------------------------------------


@router.post("/admin/departments", status_code=201)
async def create_department(
    body: DepartmentCreate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    """Create a department (admin only)."""
    from uuid import uuid4

    dept_id = uuid4()
    await db.execute(
        "INSERT INTO iam.departments (id, tenant_id, name, parent_id) "
        "VALUES (:p0, :p1, :p2, :p3)",
        dept_id,
        principal.tenant_id,
        body.name,
        body.parent_id,
    )
    return {"id": str(dept_id)}


@router.get("/admin/departments/{department_id}", status_code=200)
async def get_department(
    department_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Get department detail with member list (admin only)."""
    row = await db.fetch_one(
        "SELECT id, name, parent_id, created_at FROM iam.departments "
        "WHERE id = :p0 AND tenant_id = :p1",
        department_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="department not found")
    result = _dept_to_dict(row)
    members = await db.fetch(
        "SELECT m.user_id, m.department_id, m.role, m.joined_at, u.name, u.email "
        "FROM iam.memberships m "
        "JOIN iam.users u ON u.id = m.user_id "
        "WHERE m.department_id = :p0",
        department_id,
    )
    result["members"] = [
        {**_member_to_dict(m), "name": m.get("name"), "email": m.get("email")}
        for m in members
    ]
    return result


@router.put("/admin/departments/{department_id}", status_code=200)
async def update_department(
    department_id: UUID,
    body: DepartmentUpdate,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    """Update a department name or parent (admin only)."""
    sets: list[str] = []
    params: list[Any] = []
    if body.name is not None:
        sets.append(f"name = :p{len(params)}")
        params.append(body.name)
    if body.parent_id is not None:
        sets.append(f"parent_id = :p{len(params)}")
        params.append(body.parent_id)
    if not sets:
        return {"id": str(department_id)}
    set_sql = ", ".join(sets)
    params.extend([department_id, principal.tenant_id])
    await db.execute(
        f"UPDATE iam.departments SET {set_sql} "
        f"WHERE id = :p{len(params) - 2} AND tenant_id = :p{len(params) - 1}",
        *params,
    )
    return {"id": str(department_id)}


@router.delete("/admin/departments/{department_id}", status_code=204)
async def delete_department(
    department_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    """Delete a department (admin only). Memberships are cascade-deleted."""
    await db.execute(
        "DELETE FROM iam.departments WHERE id = :p0 AND tenant_id = :p1",
        department_id,
        principal.tenant_id,
    )


@router.post("/admin/departments/{department_id}/members", status_code=201)
async def add_member(
    department_id: UUID,
    body: MemberAdd,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    """Add a user to a department (admin only)."""
    # Verify department exists in tenant
    dept = await db.fetch_one(
        "SELECT id FROM iam.departments WHERE id = :p0 AND tenant_id = :p1",
        department_id,
        principal.tenant_id,
    )
    if dept is None:
        raise HTTPException(status_code=404, detail="department not found")
    # Verify user exists in tenant
    user = await db.fetch_one(
        "SELECT id FROM iam.users WHERE id = :p0 AND tenant_id = :p1",
        body.user_id,
        principal.tenant_id,
    )
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    await db.execute(
        "INSERT INTO iam.memberships (user_id, department_id, role) "
        "VALUES (:p0, :p1, :p2) ON CONFLICT DO NOTHING",
        body.user_id,
        department_id,
        body.role,
    )
    return {"status": "added"}


@router.delete(
    "/admin/departments/{department_id}/members/{user_id}", status_code=204
)
async def remove_member(
    department_id: UUID,
    user_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    """Remove a user from a department (admin only)."""
    del principal  # admin check done by require_admin
    await db.execute(
        "DELETE FROM iam.memberships WHERE department_id = :p0 AND user_id = :p1",
        department_id,
        user_id,
    )
