"""Authentication for mock SaaS — OAuth2 client_credentials + API Key.

Two modes are supported so the EAOS HTTP API connector (Phase 7 T2) can
exercise both credential flows against a realistic external SaaS:

1. OAuth2 ``client_credentials`` — POST /oauth/token returns a signed JWT
   (30 min validity); subsequent requests carry ``Authorization: Bearer <jwt>``.
2. Static API Key — requests carry ``X-API-Key: <key>``.

Both are checked on every protected route; either valid credential passes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.security.api_key import APIKeyHeader

from mock_saas.db import DEMO_API_KEYS, DEMO_JWT_SECRET, DEMO_OAUTH_CLIENTS

TOKEN_TTL_SECONDS: int = 30 * 60  # 30 minutes
_JWT_ALGORITHM: str = "HS256"

_bearer_scheme = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class OAuthTokenRequest:
    """Parsed body for POST /oauth/token (client_credentials grant)."""

    def __init__(self, *, grant_type: str, client_id: str, client_secret: str) -> None:
        self.grant_type = grant_type
        self.client_id = client_id
        self.client_secret = client_secret


def issue_jwt(client_id: str) -> str:
    """Sign and return a JWT carrying the client_id and a 30-min expiry."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": client_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=TOKEN_TTL_SECONDS)).timestamp()),
        "iss": "mock-saas",
    }
    return jwt.encode(payload, DEMO_JWT_SECRET, algorithm=_JWT_ALGORITHM)


def verify_jwt(token: str) -> str | None:
    """Return the client_id if the JWT is valid, else None."""
    try:
        payload = jwt.decode(token, DEMO_JWT_SECRET, algorithms=[_JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return str(sub) if isinstance(sub, str) else None


def authenticate_oauth_client(
    *, grant_type: str, client_id: str, client_secret: str
) -> str:
    """Validate client_credentials against the demo registry; return a JWT.

    Raises HTTPException 401 on any mismatch.
    """
    if grant_type != "client_credentials":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported grant_type: {grant_type}",
        )
    expected = DEMO_OAUTH_CLIENTS.get(client_id)
    if expected is None or expected != client_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid client credentials",
        )
    return issue_jwt(client_id)


async def require_auth(
    request: Request,
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    api_key: str | None = Depends(_api_key_header),
) -> str:
    """FastAPI dependency: require a valid Bearer JWT OR API Key.

    Returns the authenticated principal identifier (client_id or "apikey").
    Raises 401 if neither credential is valid.
    """
    del request  # not used; present for potential future IP-based checks
    if bearer is not None:
        client_id = verify_jwt(bearer.credentials)
        if client_id is not None:
            return client_id
    if api_key is not None and api_key in DEMO_API_KEYS:
        return "apikey"
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid credentials (Bearer token or X-API-Key required)",
        headers={"WWW-Authenticate": 'Bearer realm="mock-saas"'},
    )
