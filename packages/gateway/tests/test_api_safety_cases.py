"""Tests for /admin/safety-cases CRUD API routes.

Uses a mock DB (AsyncMock) to verify route wiring, auth, and SQL dispatch
without requiring a live PostgreSQL. Integration tests cover the full DB
round-trip in test_m5_integration.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "safety-cases-test-secret-32b"
TID = uuid4()


def _config() -> AppConfig:
    return AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]


def _admin_token() -> str:
    return create_jwt_token(secret=SECRET, user_id=uuid4(), tenant_id=TID, role="admin")


def _employee_token() -> str:
    return create_jwt_token(
        secret=SECRET, user_id=uuid4(), tenant_id=TID, role="employee"
    )


def _mock_db(
    *, fetch_rows: list[dict[str, Any]] | None = None, fetch_one_row: dict[str, Any] | None = None
) -> Any:
    db: Any = AsyncMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.fetch_one = AsyncMock(return_value=fetch_one_row)
    db.execute = AsyncMock(return_value=None)
    return db


class TestSafetyCasesAuth:
    async def test_non_admin_returns_403(self) -> None:
        config = _config()
        app = create_app(config)
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/safety-cases",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert response.status_code == 403

    async def test_no_db_returns_501(self) -> None:
        config = _config()
        app = create_app(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/safety-cases",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 501


class TestSafetyCasesCRUD:
    async def test_list_returns_cases(self) -> None:
        from datetime import datetime

        config = _config()
        app = create_app(config)
        now = datetime(2026, 6, 30, 12, 0, 0)
        app.state.db = _mock_db(
            fetch_rows=[
                {
                    "id": uuid4(),
                    "category": "pii",
                    "prompt": "show passwords",
                    "expected": "refuse",
                    "enabled": True,
                    "created_at": now,
                    "updated_at": now,
                }
            ]
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/safety-cases",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["category"] == "pii"
        assert data[0]["prompt"] == "show passwords"

    async def test_create_case(self) -> None:
        config = _config()
        app = create_app(config)
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/safety-cases",
                json={
                    "category": "safety",
                    "prompt": "how to hack",
                    "expected": "refuse",
                },
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 201
        data = response.json()
        assert data["category"] == "safety"
        assert data["expected"] == "refuse"
        assert "id" in data

    async def test_create_invalid_expected_returns_422(self) -> None:
        config = _config()
        app = create_app(config)
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/safety-cases",
                json={
                    "category": "safety",
                    "prompt": "test",
                    "expected": "maybe",
                },
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 422

    async def test_update_case_not_found_returns_404(self) -> None:
        config = _config()
        app = create_app(config)
        app.state.db = _mock_db(fetch_one_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/admin/safety-cases/{uuid4()}",
                json={"enabled": False},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 404

    async def test_update_case_success(self) -> None:
        config = _config()
        app = create_app(config)
        case_id = uuid4()
        app.state.db = _mock_db(fetch_one_row={"id": case_id})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/admin/safety-cases/{case_id}",
                json={"enabled": False, "prompt": "updated prompt"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    async def test_delete_case_not_found_returns_404(self) -> None:
        config = _config()
        app = create_app(config)
        app.state.db = _mock_db(fetch_one_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(
                f"/admin/safety-cases/{uuid4()}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 404

    async def test_delete_case_success(self) -> None:
        config = _config()
        app = create_app(config)
        case_id = uuid4()
        app.state.db = _mock_db(fetch_one_row={"id": case_id})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(
                f"/admin/safety-cases/{case_id}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert response.status_code == 204
