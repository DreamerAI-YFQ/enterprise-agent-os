"""Tests for /admin/connections CRUD + health-check API routes.

Uses a mock ConnectionManager (AsyncMock) to verify route wiring, auth,
tenant scoping, and response shape without requiring a live DB. Credentials
are never returned in responses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.data.connection_types import ConnectionRecord, HealthStatus
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "connections-test-secret-32b"
TID = uuid4()


def _config() -> AppConfig:
    return AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]


def _admin_token() -> str:
    return create_jwt_token(secret=SECRET, user_id=uuid4(), tenant_id=TID, role="admin")


def _employee_token() -> str:
    return create_jwt_token(
        secret=SECRET, user_id=uuid4(), tenant_id=TID, role="employee"
    )


def _mock_manager(
    *,
    records: list[ConnectionRecord] | None = None,
    single_record: ConnectionRecord | None = None,
    register_id: Any = None,
) -> Any:
    """Build a mock ConnectionManager."""
    mgr: Any = AsyncMock()
    mgr.register = AsyncMock(return_value=register_id or uuid4())
    mgr.update = AsyncMock(return_value=None)
    mgr.delete = AsyncMock(return_value=None)
    mgr.list = AsyncMock(return_value=records or [])
    mgr.get = AsyncMock(return_value=single_record)
    mgr.health_check = AsyncMock(
        return_value=HealthStatus(status="healthy", error=None)
    )
    return mgr


def _make_record(
    *,
    conn_id: Any = None,
    tenant_id: Any = None,
    name: str = "test-conn",
    conn_type: str = "http_api",
) -> ConnectionRecord:
    return ConnectionRecord(
        id=conn_id or uuid4(),
        tenant_id=tenant_id or TID,
        name=name,
        type=conn_type,
        config={"base_url": "https://saas.example.com"},
        health_status="unknown",
        last_health_check=None,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


def _http_config() -> dict[str, Any]:
    return {
        "base_url": "https://saas.example.com",
        "resources": {},
        "auth": {"type": "api_key", "header_name": "X-API-Key"},
    }


# ============================================================
# Auth
# ============================================================


class TestConnectionsAuth:
    async def test_non_admin_returns_403(self) -> None:
        app = create_app(_config())
        app.state.connection_manager = _mock_manager()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/connections",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert response.status_code == 403

    async def test_no_token_returns_401(self) -> None:
        app = create_app(_config())
        app.state.connection_manager = _mock_manager()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/admin/connections")
        assert response.status_code == 401

    async def test_manager_not_configured_returns_501(self) -> None:
        app = create_app(_config())
        # Don't set app.state.connection_manager
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/connections",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 501


# ============================================================
# CRUD
# ============================================================


class TestConnectionsCRUD:
    async def test_create_connection(self) -> None:
        conn_id = uuid4()
        app = create_app(_config())
        app.state.connection_manager = _mock_manager(register_id=conn_id)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/connections",
                json={
                    "name": "erp-conn",
                    "type": "http_api",
                    "config": _http_config(),
                    "credentials": {"api_key": "secret"},
                },
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 201
        assert response.json()["id"] == str(conn_id)

    async def test_create_invalid_type_returns_422(self) -> None:
        app = create_app(_config())
        app.state.connection_manager = _mock_manager()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/connections",
                json={"name": "bad", "type": "invalid", "config": {}},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 422

    async def test_list_connections(self) -> None:
        record = _make_record(name="conn1")
        app = create_app(_config())
        app.state.connection_manager = _mock_manager(records=[record])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/connections",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "conn1"
        assert "credentials" not in data[0]

    async def test_get_connection(self) -> None:
        conn_id = uuid4()
        record = _make_record(conn_id=conn_id, name="detail-conn")
        app = create_app(_config())
        app.state.connection_manager = _mock_manager(single_record=record)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/admin/connections/{conn_id}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 200
        assert response.json()["name"] == "detail-conn"

    async def test_get_not_found_returns_404(self) -> None:
        app = create_app(_config())
        app.state.connection_manager = _mock_manager(single_record=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/admin/connections/{uuid4()}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 404

    async def test_update_connection(self) -> None:
        conn_id = uuid4()
        existing = _make_record(conn_id=conn_id)
        app = create_app(_config())
        app.state.connection_manager = _mock_manager(single_record=existing)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/admin/connections/{conn_id}",
                json={"name": "updated-name"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 200

    async def test_delete_connection(self) -> None:
        conn_id = uuid4()
        existing = _make_record(conn_id=conn_id)
        app = create_app(_config())
        app.state.connection_manager = _mock_manager(single_record=existing)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(
                f"/admin/connections/{conn_id}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 204


# ============================================================
# Health check
# ============================================================


class TestConnectionsHealthCheck:
    async def test_trigger_health_check(self) -> None:
        conn_id = uuid4()
        existing = _make_record(conn_id=conn_id)
        app = create_app(_config())
        app.state.connection_manager = _mock_manager(single_record=existing)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/admin/connections/{conn_id}/health-check",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["id"] == str(conn_id)

    async def test_health_check_not_found(self) -> None:
        app = create_app(_config())
        app.state.connection_manager = _mock_manager(single_record=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/admin/connections/{uuid4()}/health-check",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 404


# ============================================================
# Tenant safety
# ============================================================


class TestConnectionsTenantSafety:
    async def test_get_tenant_mismatch_returns_403(self) -> None:
        other_tenant = uuid4()
        record = _make_record(conn_id=uuid4(), tenant_id=other_tenant)
        app = create_app(_config())
        app.state.connection_manager = _mock_manager(single_record=record)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/admin/connections/{record.id}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 403

    async def test_credentials_never_in_response(self) -> None:
        """List and get responses must never contain a 'credentials' key."""
        record = _make_record(name="leak-test")
        app = create_app(_config())
        app.state.connection_manager = _mock_manager(
            records=[record], single_record=record
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {"Authorization": f"Bearer {_admin_token()}"}
            list_resp = await client.get("/admin/connections", headers=headers)
            get_resp = await client.get(
                f"/admin/connections/{record.id}", headers=headers
            )
        for resp in (list_resp, get_resp):
            body = resp.json()
            if isinstance(body, list):
                for item in body:
                    assert "credentials" not in item
            else:
                assert "credentials" not in body
