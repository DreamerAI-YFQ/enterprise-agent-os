"""Tests for /tasks API — unified task list aggregating approvals + sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "tasks-test-secret-32bytes!!!"
TID = UUID("00000000-0000-0000-0000-000000000001")
ADMIN_ID = UUID("00000000-0000-0000-0000-000000000010")
EMP_ID = UUID("00000000-0000-0000-0000-000000000020")
AGENT_ID = UUID("00000000-0000-0000-0000-000000000100")


def _config() -> AppConfig:
    return AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]


def _admin_token() -> str:
    return create_jwt_token(SECRET, ADMIN_ID, TID, "admin")


def _employee_token() -> str:
    return create_jwt_token(SECRET, EMP_ID, TID, "employee")


def _mock_db(*, fetch_rows: list[dict[str, Any]] | None = None) -> Any:
    """Build a mock DbClient — fetch returns scripted rows."""
    db: Any = AsyncMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.fetch_one = AsyncMock(return_value=None)
    db.execute = AsyncMock(return_value=None)
    return db


def _approval_row(
    *, approval_id: UUID | None = None, requested_by: UUID = EMP_ID
) -> dict[str, Any]:
    return {
        "id": approval_id or uuid4(),
        "tenant_id": TID,
        "agent_id": AGENT_ID,
        "session_id": uuid4(),
        "reason": "high_risk",
        "status": "pending",
        "requested_by": requested_by,
        "decided_by": None,
        "decided_at": None,
        "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    }


def _session_row(
    *,
    session_id: UUID | None = None,
    status: str = "active",
    title: str = "Test session",
) -> dict[str, Any]:
    return {
        "id": session_id or uuid4(),
        "agent_id": AGENT_ID,
        "tenant_id": TID,
        "user_id": EMP_ID,
        "title": title,
        "status": status,
        "thread_id": "thread-1",
        "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        "last_active_at": datetime(2026, 7, 1, 12, 30, tzinfo=UTC),
    }


class TestListTasks:
    async def test_no_token_returns_401(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/tasks")
        assert resp.status_code == 401

    async def test_pending_returns_approvals(self) -> None:
        approvals = [_approval_row()]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=approvals)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/tasks?status=pending",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["type"] == "approval"
        assert data[0]["status"] == "pending"

    async def test_running_returns_active_sessions(self) -> None:
        sessions = [_session_row(status="active")]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=sessions)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/tasks?status=running",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["type"] == "session"
        assert data[0]["status"] == "running"

    async def test_completed_returns_closed_sessions(self) -> None:
        sessions = [_session_row(status="completed", title="Done task")]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=sessions)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/tasks?status=completed",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "completed"

    async def test_no_filter_merges_all(self) -> None:
        """Without status filter, the route makes 3 fetch calls (pending,
        running, completed). Use side_effect to return different rows per call."""
        approvals = [_approval_row()]
        running = [_session_row(status="active", title="Running")]
        completed = [_session_row(status="completed", title="Done")]
        app = create_app(_config())
        db: Any = AsyncMock()
        db.fetch = AsyncMock(side_effect=[approvals, running, completed])
        db.fetch_one = AsyncMock(return_value=None)
        db.execute = AsyncMock(return_value=None)
        app.state.db = db
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/tasks",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        types = {t["type"] for t in data}
        assert types == {"approval", "session"}

    async def test_empty_results(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=[])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/tasks?status=pending",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []
