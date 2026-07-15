"""Current-user API — profile info and personal preferences.

- ``GET /me`` — return the authenticated user's profile + preferences
- ``PUT /me/preferences`` — save personal settings (theme, language, etc.)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db, get_principal
from fastapi import APIRouter, Depends, HTTPException  # noqa: TC002
from pydantic import BaseModel

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(tags=["me"])


class PreferencesUpdate(BaseModel):
    """Arbitrary JSON object of personal preferences."""

    preferences: dict[str, Any]


@router.get("/me", status_code=200)
async def get_me(
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Return the current user's profile and preferences."""
    row = await db.fetch_one(
        "SELECT id, tenant_id, email, name, role, status, preferences "
        "FROM iam.users WHERE id = :p0 AND tenant_id = :p1",
        principal.user_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    prefs = row.get("preferences")
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "status": row.get("status", "active"),
        "preferences": prefs if isinstance(prefs, dict) else {},
    }


@router.put("/me/preferences", status_code=200)
async def update_preferences(
    body: PreferencesUpdate,
    principal: Principal = Depends(get_principal),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Save the current user's personal preferences."""
    import json

    await db.execute(
        "UPDATE iam.users SET preferences = CAST(:p0 AS jsonb) "
        "WHERE id = :p1 AND tenant_id = :p2",
        json.dumps(body.preferences, ensure_ascii=False),
        principal.user_id,
        principal.tenant_id,
    )
    return {"preferences": body.preferences}
