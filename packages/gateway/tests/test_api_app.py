"""Tests for FastAPI app — health, ready, auth middleware, /me endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:
    from uuid import UUID

    import pytest


def _config(*, secret: str = "test-secret") -> AppConfig:
    return AppConfig(secret_key=secret, debug=True)  # type: ignore[call-arg]


def _mock_user_db(
    user_id: UUID, tenant_id: UUID, role: str = "employee"
) -> Any:
    """Mock db that returns a user row for /me lookups."""
    db: Any = AsyncMock()
    db.fetch_one = AsyncMock(
        return_value={
            "id": user_id,
            "tenant_id": tenant_id,
            "email": "test@test.com",
            "name": "Test User",
            "role": role,
            "status": "active",
            "preferences": {},
        }
    )
    db.fetch = AsyncMock(return_value=[])
    db.fetch_val = AsyncMock(return_value=0)
    db.execute = AsyncMock(return_value=None)
    return db


def _token(
    config: AppConfig,
    *,
    role: str = "employee",
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
) -> str:
    return create_jwt_token(
        secret=config.secret_key,
        user_id=user_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        role=role,
    )


class TestHealthEndpoints:
    async def test_health_returns_ok(self) -> None:
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_ready_returns_ready(self) -> None:
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}


class TestAuthMiddleware:
    async def test_protected_route_without_token_returns_401(self) -> None:
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/me")
        assert response.status_code == 401
        assert response.json() == {
            "detail": "missing authorization header",
            "code": "unauthorized",
        }

    async def test_protected_route_with_invalid_scheme_returns_401(self) -> None:
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/me", headers={"Authorization": "Basic xyz"}
            )
        assert response.status_code == 401
        assert response.json() == {
            "detail": "invalid authorization scheme",
            "code": "unauthorized",
        }

    async def test_protected_route_with_invalid_token_returns_401(self) -> None:
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/me", headers={"Authorization": "Bearer invalid-token"}
            )
        assert response.status_code == 401
        assert response.json() == {
            "detail": "invalid token",
            "code": "unauthorized",
        }

    async def test_valid_token_returns_principal_info(self) -> None:
        config = _config()
        app = create_app(config)
        uid = uuid4()
        tid = uuid4()
        token = _token(config, user_id=uid, tenant_id=tid, role="manager")
        app.state.db = _mock_user_db(uid, tid, "manager")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/me", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(uid)
        assert data["tenant_id"] == str(tid)
        assert data["role"] == "manager"

    async def test_expired_token_returns_401(self) -> None:
        config = _config()
        app = create_app(config)
        token = create_jwt_token(
            secret=config.secret_key,
            user_id=uuid4(),
            tenant_id=uuid4(),
            role="employee",
            expires_in=-1,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/me", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 401


class TestWhitelist:
    async def test_health_no_auth_needed(self) -> None:
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")
        assert response.status_code == 200

    async def test_ready_no_auth_needed(self) -> None:
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/ready")
        assert response.status_code == 200

    async def test_webhook_prefix_no_auth_needed(self) -> None:
        """Webhook paths bypass auth — route executes without 401."""
        from unittest.mock import AsyncMock

        gateway = AsyncMock()
        gateway.handle_webhook.return_value = {
            "status": "accepted",
            "message_id": "test",
        }
        app = create_app(_config())
        app.state.gateway = gateway
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/webhook/dingtalk", json={})
        assert response.status_code != 401


class TestServiceToken:
    async def test_service_token_bypasses_jwt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EAOS_SERVICE_TOKEN", "svc-secret-token")
        app = create_app(_config())
        # /me does a db lookup; mock a user row with admin role
        app.state.db = _mock_user_db(uuid4(), uuid4(), "admin")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/me", headers={"Authorization": "Bearer svc-secret-token"}
            )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"
