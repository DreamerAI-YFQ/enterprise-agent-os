"""FastAPI application for mock SaaS — REST API entrypoint.

Exposes:
  - GET  /health            (unauthenticated health check)
  - POST /oauth/token       (client_credentials → JWT)
  - /api/v1/orders          (CRUD, auth required)
  - /api/v1/customers       (CRUD, auth required)
  - /api/v1/inventory       (read + update, auth required)

Run::

    uvicorn mock_saas.main:app --host 0.0.0.0 --port 18000
"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, status
from pydantic import BaseModel

from mock_saas.auth import authenticate_oauth_client
from mock_saas.db import get_db
from mock_saas.routes import customers_router, inventory_router, orders_router


class OAuthTokenResponse(BaseModel):
    """RFC 6749 token response shape."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 1800  # 30 minutes


def create_app() -> FastAPI:
    """Build the FastAPI app. Separate from module-level ``app`` for testability."""
    app = FastAPI(
        title="Mock SaaS",
        description="Simulated ERP/CRM external system for EAOS Phase 7",
        version="0.1.0",
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "mock-saas"}

    @app.post("/oauth/token", tags=["auth"], response_model=OAuthTokenResponse)
    async def oauth_token(
        grant_type: Annotated[str, Form()],
        client_id: Annotated[str, Form()],
        client_secret: Annotated[str, Form()],
    ) -> OAuthTokenResponse:
        token = authenticate_oauth_client(
            grant_type=grant_type,
            client_id=client_id,
            client_secret=client_secret,
        )
        return OAuthTokenResponse(access_token=token)

    app.include_router(orders_router)
    app.include_router(customers_router)
    app.include_router(inventory_router)

    return app


app = create_app()


def _ensure_seed() -> None:
    """Touch the DB singleton so demo data is seeded on import."""
    try:
        get_db()
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to seed mock data: {exc}",
        ) from exc


_ensure_seed()
