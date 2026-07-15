"""Tests for /me, /admin/metrics, /admin/users, and /admin/config API routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "f0-t12-t13-t14-secret-32byte!"
TID = UUID("00000000-0000-0000-0000-000000000001")
ADMIN_ID = UUID("00000000-0000-0000-0000-000000000010")
EMP_ID = UUID("00000000-0000-0000-0000-000000000020")


def _config() -> AppConfig:
    return AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]


def _admin_token() -> str:
    return create_jwt_token(SECRET, ADMIN_ID, TID, "admin")


def _employee_token() -> str:
    return create_jwt_token(SECRET, EMP_ID, TID, "employee")


def _mock_db(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    single_row: dict[str, Any] | None = None,
    val: Any = 0,
) -> Any:
    db: Any = AsyncMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.fetch_one = AsyncMock(return_value=single_row)
    db.fetch_val = AsyncMock(return_value=val)
    db.execute = AsyncMock(return_value=None)
    return db


# ============================================================
# /me
# ============================================================


class TestMe:
    async def test_get_me(self) -> None:
        row = {
            "id": EMP_ID,
            "tenant_id": TID,
            "email": "emp@test.com",
            "name": "Employee",
            "role": "employee",
            "status": "active",
            "preferences": {"theme": "dark"},
        }
        app = create_app(_config())
        app.state.db = _mock_db(single_row=row)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/me",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "emp@test.com"
        assert data["preferences"] == {"theme": "dark"}

    async def test_put_preferences(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/me/preferences",
                json={"preferences": {"theme": "light", "lang": "en"}},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json()["preferences"]["theme"] == "light"
        app.state.db.execute.assert_awaited()

    async def test_no_token_returns_401(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/me")
        assert resp.status_code == 401


# ============================================================
# /admin/metrics
# ============================================================


class TestMetrics:
    async def test_get_metrics(self) -> None:
        db: Any = AsyncMock()
        # fetch_val is called 7 times for counts
        db.fetch_val = AsyncMock(
            side_effect=[10, 5, 30, 8, 12, 3, 2]
        )
        db.fetch = AsyncMock(
            return_value=[
                {"day": datetime(2026, 7, 1, tzinfo=UTC), "count": 5}
            ]
        )
        app = create_app(_config())
        app.state.db = db
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/metrics",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["users"] == 10
        assert data["counts"]["agents"] == 5
        assert data["counts"]["sessions"] == 30
        assert data["counts"]["pending_approvals"] == 3
        assert len(data["activity_7d"]) == 1
        assert data["activity_7d"][0]["sessions"] == 5

    async def test_employee_forbidden(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/metrics",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403


# ============================================================
# /admin/users
# ============================================================


def _user_row(*, user_id: UUID | None = None, email: str = "user@test.com") -> dict[str, Any]:
    return {
        "id": user_id or uuid4(),
        "tenant_id": TID,
        "email": email,
        "name": "Test User",
        "role": "employee",
        "status": "active",
        "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    }


class TestUsers:
    async def test_list_users(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=[_user_row(), _user_row(email="b@test.com")])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/users",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_create_user(self) -> None:
        new_id = uuid4()
        app = create_app(_config())
        # fetch_one called twice: 1st=duplicate check (None), 2nd=INSERT RETURNING (row)
        db = _mock_db()
        db.fetch_one = AsyncMock(
            side_effect=[None, _user_row(user_id=new_id, email="new@test.com")]
        )
        app.state.db = db
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/users",
                json={"email": "new@test.com", "name": "New User"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 201
        assert resp.json()["email"] == "new@test.com"

    async def test_create_duplicate_email(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row={"id": uuid4()})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/users",
                json={"email": "dup@test.com", "name": "Dup"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 409

    async def test_update_user(self) -> None:
        uid = uuid4()
        existing = _user_row(user_id=uid)
        updated = {**existing, "name": "Updated"}
        app = create_app(_config())
        # fetch_one called twice: 1st=existence check, 2nd=fetch updated row
        db = _mock_db()
        db.fetch_one = AsyncMock(side_effect=[existing, updated])
        app.state.db = db
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/admin/users/{uid}",
                json={"name": "Updated"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    async def test_delete_user(self) -> None:
        uid = uuid4()
        app = create_app(_config())
        app.state.db = _mock_db(single_row={"id": uid})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                f"/admin/users/{uid}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 204

    async def test_employee_forbidden(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/users",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403


# ============================================================
# /admin/models + /admin/plugins
# ============================================================


class TestConfigSettings:
    async def test_get_models_empty(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/models",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json() == {}

    async def test_put_models(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/admin/models",
                json={"default_model": "gpt-4", "providers": {"openai": {}}},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json()["default_model"] == "gpt-4"
        app.state.db.execute.assert_awaited()

    async def test_get_plugins(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row={"value": '{"dingtalk": {"enabled": true}}'})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/plugins",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json()["dingtalk"]["enabled"] is True

    async def test_put_plugins(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/admin/plugins",
                json={"plugins": {"slack": {"enabled": False}}},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        app.state.db.execute.assert_awaited()


# ============================================================
# /admin/mcp/connectors
# ============================================================


class TestMcpConnectors:
    async def test_list_connectors(self) -> None:
        rows = [
            {
                "id": uuid4(),
                "name": "erp-mcp",
                "type": "mcp_stdio",
                "config": '{"command": "python"}',
                "health_status": "healthy",
                "last_health_check": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
                "created_at": datetime(2026, 7, 1, 11, 0, tzinfo=UTC),
            }
        ]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=rows)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/mcp/connectors",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "erp-mcp"
        assert data[0]["config"]["command"] == "python"


# ============================================================
# /admin/report-templates
# ============================================================


def _template_row(*, tpl_id: UUID | None = None) -> dict[str, Any]:
    return {
        "id": tpl_id or uuid4(),
        "tenant_id": TID,
        "name": "Monthly Report",
        "description": "Monthly summary",
        "template_type": "summary",
        "content": '{"sections": ["overview"]}',
        "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    }


class TestReportTemplates:
    async def test_list_templates(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=[_template_row()])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/report-templates",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_create_template(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=_template_row())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/report-templates",
                json={"name": "Monthly Report", "template_type": "summary"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Monthly Report"

    async def test_delete_template(self) -> None:
        tpl_id = uuid4()
        app = create_app(_config())
        app.state.db = _mock_db(single_row={"id": tpl_id})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                f"/admin/report-templates/{tpl_id}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 204

    async def test_delete_not_found(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                f"/admin/report-templates/{uuid4()}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 404
