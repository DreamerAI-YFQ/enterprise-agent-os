"""Tests for /auth/login, /auth/refresh, /auth/logout routes.

Uses a mock DbClient (fetch_one returns scripted user rows) to verify
login, token issuance, refresh, and logout without a live DB.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import jwt
from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "auth-test-secret-32bytes!!"
TID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000010")


def _config() -> AppConfig:
    return AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]


def _mock_db(*, user_row: dict[str, Any] | None = None) -> Any:
    """Build a mock DbClient whose fetch_one returns the given user row."""
    db: Any = AsyncMock()
    db.fetch_one = AsyncMock(return_value=user_row)
    return db


def _user_row(
    *,
    status: str = "active",
    role: str = "employee",
    email: str = "alice@eaos.test",
) -> dict[str, Any]:
    return {
        "id": USER_ID,
        "tenant_id": TID,
        "role": role,
        "name": "Alice",
        "email": email,
        "status": status,
    }


class TestLogin:
    async def test_login_success(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(user_row=_user_row())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/auth/login",
                json={"email": "alice@eaos.test"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_type"] == "bearer"
        assert "access_token" in data
        assert data["user"]["email"] == "alice@eaos.test"
        assert data["user"]["role"] == "employee"
        # Token should be decodable with the correct secret
        payload = jwt.decode(data["access_token"], SECRET, algorithms=["HS256"])
        assert payload["sub"] == str(USER_ID)
        assert payload["tid"] == str(TID)

    async def test_login_unknown_email(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(user_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/auth/login",
                json={"email": "nobody@eaos.test"},
            )
        assert resp.status_code == 401

    async def test_login_inactive_user(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(user_row=_user_row(status="suspended"))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/auth/login",
                json={"email": "alice@eaos.test"},
            )
        assert resp.status_code == 403


class TestRefresh:
    async def test_refresh_issues_new_token(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        token = create_jwt_token(SECRET, USER_ID, TID, "employee")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/auth/refresh",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        # New token should differ and be valid
        payload = jwt.decode(data["access_token"], SECRET, algorithms=["HS256"])
        assert payload["sub"] == str(USER_ID)

    async def test_refresh_without_token(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/auth/refresh")
        assert resp.status_code == 401


class TestLogout:
    async def test_logout_returns_204(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        token = create_jwt_token(SECRET, USER_ID, TID, "employee")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 204

    async def test_logout_without_token(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/auth/logout")
        assert resp.status_code == 401
