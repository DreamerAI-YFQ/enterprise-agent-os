"""Tests for /notifications API — list, mark read, mark all read."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "notif-test-secret-32bytes!"
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
) -> Any:
    db: Any = AsyncMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.fetch_one = AsyncMock(return_value=single_row)
    db.execute = AsyncMock(return_value=None)
    return db


def _notif_row(
    *,
    notif_id: UUID | None = None,
    read_at: datetime | None = None,
    title: str = "Test notification",
    notif_type: str = "system",
) -> dict[str, Any]:
    return {
        "id": notif_id or uuid4(),
        "tenant_id": TID,
        "user_id": EMP_ID,
        "type": notif_type,
        "title": title,
        "body": "Something happened",
        "related_entity_type": "approval",
        "related_entity_id": uuid4(),
        "read_at": read_at,
        "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    }


class TestListNotifications:
    async def test_list_notifications(self) -> None:
        rows = [
            _notif_row(title="Unread"),
            _notif_row(title="Read", read_at=datetime(2026, 7, 1, 13, tzinfo=UTC)),
        ]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=rows)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/notifications",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["title"] == "Unread"
        assert data[0]["read"] is False
        assert data[1]["read"] is True

    async def test_unread_only_filter(self) -> None:
        rows = [_notif_row(title="Unread")]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=rows)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/notifications?unread_only=true",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_empty_list(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=[])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/notifications",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_no_token_returns_401(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/notifications")
        assert resp.status_code == 401


class TestMarkRead:
    async def test_mark_single_read(self) -> None:
        nid = uuid4()
        row = _notif_row(notif_id=nid)
        app = create_app(_config())
        app.state.db = _mock_db(single_row=row)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/notifications/{nid}/read",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        # execute should have been called to set read_at
        app.state.db.execute.assert_awaited()

    async def test_mark_read_not_found(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/notifications/{uuid4()}/read",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 404

    async def test_mark_all_read(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/notifications/read-all",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        app.state.db.execute.assert_awaited()
