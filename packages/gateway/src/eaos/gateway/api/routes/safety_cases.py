"""Safety cases admin API — CRUD for dynamic compliance test cases.

Safety cases stored in ``harness.safety_cases`` (migration 0005) override
the bundled YAML file when present. These routes let admins hot-add/remove
test cases per tenant without redeploying.

All routes require the admin role and read/write against the DB via
``app.state.db``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002 — runtime for FastAPI
from eaos.gateway.api.routes.admin import require_admin
from fastapi import APIRouter, Depends, HTTPException, Request  # noqa: TC002
from pydantic import BaseModel

router = APIRouter(prefix="/admin/safety-cases", tags=["admin/safety-cases"])


class SafetyCaseCreate(BaseModel):
    """Request body for creating a safety case."""

    category: str
    prompt: str
    expected: str  # "refuse" | "answer"
    enabled: bool = True


class SafetyCaseUpdate(BaseModel):
    """Request body for updating a safety case."""

    category: str | None = None
    prompt: str | None = None
    expected: str | None = None
    enabled: bool | None = None


_VALID_EXPECTED = {"refuse", "answer"}


def _db(request: Request) -> Any:
    """Fetch the DB client from app.state; 501 if not wired."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(
            status_code=501, detail="db not configured on this instance"
        )
    return db


@router.get("", status_code=200)
async def list_safety_cases(
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, Any]]:
    """List all safety cases for the tenant (including disabled)."""
    db = _db(request)
    rows = await db.fetch(
        "SELECT id, category, prompt, expected, enabled, created_at, updated_at "
        "FROM harness.safety_cases WHERE tenant_id = :p0 ORDER BY created_at",
        principal.tenant_id,
    )
    return [
        {
            "id": str(row["id"]),
            "category": row["category"],
            "prompt": row["prompt"],
            "expected": row["expected"],
            "enabled": row["enabled"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]


@router.post("", status_code=201)
async def create_safety_case(
    body: SafetyCaseCreate,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    """Create a new safety case for the tenant."""
    if body.expected not in _VALID_EXPECTED:
        raise HTTPException(
            status_code=422,
            detail=f"expected must be one of {sorted(_VALID_EXPECTED)}",
        )
    db = _db(request)
    case_id = uuid4()
    await db.execute(
        """INSERT INTO harness.safety_cases
           (id, tenant_id, category, prompt, expected, enabled)
           VALUES (:p0, :p1, :p2, :p3, :p4, :p5)""",
        case_id,
        principal.tenant_id,
        body.category,
        body.prompt,
        body.expected,
        body.enabled,
    )
    return {
        "id": str(case_id),
        "category": body.category,
        "prompt": body.prompt,
        "expected": body.expected,
        "enabled": body.enabled,
    }


@router.put("/{case_id}", status_code=200)
async def update_safety_case(
    case_id: UUID,
    body: SafetyCaseUpdate,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    """Update an existing safety case (partial update)."""
    if body.expected is not None and body.expected not in _VALID_EXPECTED:
        raise HTTPException(
            status_code=422,
            detail=f"expected must be one of {sorted(_VALID_EXPECTED)}",
        )
    db = _db(request)
    existing = await db.fetch_one(
        "SELECT id FROM harness.safety_cases WHERE id = :p0 AND tenant_id = :p1",
        case_id,
        principal.tenant_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="safety case not found")

    sets: list[str] = []
    params: list[Any] = []
    if body.category is not None:
        sets.append("category = :p" + str(len(params)))
        params.append(body.category)
    if body.prompt is not None:
        sets.append("prompt = :p" + str(len(params)))
        params.append(body.prompt)
    if body.expected is not None:
        sets.append("expected = :p" + str(len(params)))
        params.append(body.expected)
    if body.enabled is not None:
        sets.append("enabled = :p" + str(len(params)))
        params.append(body.enabled)
    if not sets:
        return {"id": str(case_id), "status": "unchanged"}
    sets.append("updated_at = NOW()")
    params.extend([case_id, principal.tenant_id])
    sql = (
        "UPDATE harness.safety_cases SET "
        + ", ".join(sets)
        + " WHERE id = :p"
        + str(len(params) - 2)
        + " AND tenant_id = :p"
        + str(len(params) - 1)
    )
    await db.execute(sql, *params)
    return {"id": str(case_id), "status": "updated"}


@router.delete("/{case_id}", status_code=204)
async def delete_safety_case(
    case_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> None:
    """Delete a safety case."""
    db = _db(request)
    existing = await db.fetch_one(
        "SELECT id FROM harness.safety_cases WHERE id = :p0 AND tenant_id = :p1",
        case_id,
        principal.tenant_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="safety case not found")
    await db.execute(
        "DELETE FROM harness.safety_cases WHERE id = :p0 AND tenant_id = :p1",
        case_id,
        principal.tenant_id,
    )
