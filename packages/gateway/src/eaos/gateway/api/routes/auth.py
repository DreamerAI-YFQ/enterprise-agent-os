"""Authentication API — login, refresh, logout.

``POST /auth/login`` looks up a user by email in ``iam.users`` and issues a
JWT (HS256). ``POST /auth/refresh`` re-issues a token from the current
Principal. ``POST /auth/logout`` is a stateless no-op (clients drop the
token).

Login is whitelisted in JWTAuthMiddleware (no auth required to log in).
Refresh and logout require a valid token.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eaos.core.auth import Principal, create_jwt_token  # noqa: TC002
from eaos.gateway.api.deps import get_principal
from fastapi import APIRouter, Depends, HTTPException, Request  # noqa: TC002
from pydantic import BaseModel

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Login request body — email-based (prototype: no password)."""

    email: str


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"
    user: dict[str, str] | None = None


@router.post("/login", response_model=TokenResponse, status_code=200)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    """Authenticate by email and issue a JWT.

    Looks up ``iam.users`` by email; issues a token if the user is active.
    """
    db: DbClient = request.app.state.db
    secret: str = request.app.state.config.secret_key

    row = await db.fetch_one(
        "SELECT id, tenant_id, role, name, email, status "
        "FROM iam.users WHERE email = :p0",
        body.email,
    )
    if row is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    if row.get("status") != "active":
        raise HTTPException(status_code=403, detail="user account is not active")

    user_id = row["id"]
    tenant_id = row["tenant_id"]
    role = str(row["role"])

    token = create_jwt_token(secret, user_id, tenant_id, role)
    return TokenResponse(
        access_token=token,
        user={
            "id": str(user_id),
            "tenant_id": str(tenant_id),
            "role": role,
            "name": str(row.get("name") or ""),
            "email": str(row.get("email") or ""),
        },
    )


@router.post("/refresh", response_model=TokenResponse, status_code=200)
async def refresh(
    request: Request,
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> TokenResponse:
    """Issue a new JWT from the current valid token."""
    secret: str = request.app.state.config.secret_key
    token = create_jwt_token(
        secret, principal.user_id, principal.tenant_id, principal.role
    )
    return TokenResponse(access_token=token)


@router.post("/logout", status_code=204)
async def logout(
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> None:
    """Stateless logout — client discards the token."""
    del principal
