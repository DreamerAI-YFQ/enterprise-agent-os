"""Tests for /auth/login, /auth/refresh, /auth/logout routes.

Uses a mock DbClient (fetch_one returns scripted user rows) to verify
login, token issuance, refresh, and logout without a live DB.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import jwt
from eaos.core.auth import create_jwt_token, hash_password
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "auth-test-secret-with-at-least-32-bytes"
TID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000010")
TENANT_SLUG = "acme-corp"
PASSWORD = "test-password"
PASSWORD_HASH = hash_password(PASSWORD)
_DEFAULT_IDENTITY = object()


def _config() -> AppConfig:
    return AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]


def _mock_db(
    *,
    user_row: dict[str, Any] | None = None,
    identity_row: dict[str, Any] | None | object = _DEFAULT_IDENTITY,
) -> Any:
    """Build a mock DbClient whose fetch_one returns the given user row."""
    db: Any = AsyncMock()
    vars(db)["_eaos_validate_identity"] = True
    active_identity = {
        "role": "employee",
        "status": "active",
        "tenant_status": "active",
    }

    async def fetch_one(sql: str, *params: Any) -> dict[str, Any] | None:
        del params
        if "FROM iam.users u" in sql and "tenant_status" in sql:
            if identity_row is _DEFAULT_IDENTITY:
                return active_identity
            return identity_row if isinstance(identity_row, dict) else None
        return user_row

    db.fetch_one = AsyncMock(side_effect=fetch_one)
    db.fetch = AsyncMock(return_value=[])
    return db


def _user_row(
    *,
    status: str = "active",
    role: str = "employee",
    email: str = "alice@eaos.test",
    password_hash: str | None = PASSWORD_HASH,
) -> dict[str, Any]:
    return {
        "id": USER_ID,
        "tenant_id": TID,
        "role": role,
        "name": "Alice",
        "email": email,
        "status": status,
        "password_hash": password_hash,
    }


class TestLogin:
    async def test_login_success(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(user_row=_user_row())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/auth/login",
                json={
                    "tenant_slug": TENANT_SLUG,
                    "email": "alice@eaos.test",
                    "password": PASSWORD,
                },
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
        login_call = app.state.db.fetch_one.await_args_list[0]
        assert login_call.args[1:] == (TENANT_SLUG, "alice@eaos.test")

    async def test_login_unknown_email(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(user_row=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/auth/login",
                json={
                    "tenant_slug": TENANT_SLUG,
                    "email": "nobody@eaos.test",
                    "password": PASSWORD,
                },
            )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "invalid credentials"

    async def test_login_wrong_password_uses_same_401(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(user_row=_user_row())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/auth/login",
                json={
                    "tenant_slug": TENANT_SLUG,
                    "email": "alice@eaos.test",
                    "password": "wrong",
                },
            )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "invalid credentials"

    async def test_login_unusable_hash_fails_closed(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(user_row=_user_row(password_hash="legacy-or-corrupt"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/auth/login",
                json={
                    "tenant_slug": TENANT_SLUG,
                    "email": "alice@eaos.test",
                    "password": PASSWORD,
                },
            )
        assert resp.status_code == 401

    async def test_login_requires_password_and_tenant(self) -> None:
        app = create_app(_config())
        db = _mock_db(user_row=_user_row())
        app.state.db = db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            missing_password = await client.post(
                "/auth/login",
                json={"tenant_slug": TENANT_SLUG, "email": "alice@eaos.test"},
            )
            missing_tenant = await client.post(
                "/auth/login",
                json={"email": "alice@eaos.test", "password": PASSWORD},
            )
        assert missing_password.status_code == 422
        assert missing_tenant.status_code == 422
        db.fetch_one.assert_not_awaited()

    async def test_login_inactive_user(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(user_row=_user_row(status="suspended"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/auth/login",
                json={
                    "tenant_slug": TENANT_SLUG,
                    "email": "alice@eaos.test",
                    "password": PASSWORD,
                },
            )
        assert resp.status_code == 403

    async def test_inactive_status_not_disclosed_for_wrong_password(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(user_row=_user_row(status="suspended"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/auth/login",
                json={
                    "tenant_slug": TENANT_SLUG,
                    "email": "alice@eaos.test",
                    "password": "wrong",
                },
            )
        assert resp.status_code == 401


class TestRefresh:
    async def test_refresh_issues_new_token(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        token = create_jwt_token(SECRET, USER_ID, TID, "employee")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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

    async def test_refresh_uses_current_database_role(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(
            identity_row={
                "role": "admin",
                "status": "active",
                "tenant_status": "active",
            }
        )
        token = create_jwt_token(SECRET, USER_ID, TID, "employee")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/auth/refresh",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        payload = jwt.decode(resp.json()["access_token"], SECRET, algorithms=["HS256"])
        assert payload["role"] == "admin"

    async def test_refresh_rejects_suspended_user(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(
            identity_row={
                "role": "employee",
                "status": "suspended",
                "tenant_status": "active",
            }
        )
        token = create_jwt_token(SECRET, USER_ID, TID, "employee")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/auth/refresh",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 401

    async def test_refresh_rejects_deleted_user(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(identity_row=None)
        token = create_jwt_token(SECRET, USER_ID, TID, "employee")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/auth/refresh",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 401

    async def test_refresh_without_token(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/auth/refresh")
        assert resp.status_code == 401


class TestLogout:
    async def test_logout_returns_204(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        token = create_jwt_token(SECRET, USER_ID, TID, "employee")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 204

    async def test_logout_without_token(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/auth/logout")
        assert resp.status_code == 401
