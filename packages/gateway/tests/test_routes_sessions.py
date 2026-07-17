"""Tests for /sessions API routes — list, detail, messages, delete, rename.

Uses a mock DbClient (fetch/fetch_one/execute are AsyncMock) to verify route
wiring, auth, tenant + user scoping, and response shape without a live DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "sessions-test-secret-32bytes!"
TID = UUID("00000000-0000-0000-0000-000000000001")
ADMIN_ID = UUID("00000000-0000-0000-0000-000000000010")
EMP_ID = UUID("00000000-0000-0000-0000-000000000020")
OTHER_EMP_ID = UUID("00000000-0000-0000-0000-000000000021")
AGENT_ID = UUID("00000000-0000-0000-0000-000000000100")


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
    """Build a mock DbClient with configurable fetch/fetch_one results."""
    db: Any = AsyncMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.fetch_all = AsyncMock(return_value=[])
    db.fetch_one = AsyncMock(return_value=single_row)
    db.execute = AsyncMock(return_value=None)
    return db


class _StubSpan:
    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _StubTracer:
    async def span(self, *_args: Any, **_kwargs: Any) -> Any:
        yield _StubSpan()


def _wire_invoke_dependencies(app: Any, runner: Any) -> None:
    app.state.runner = runner
    # Explicitly wire every required production dependency. Using the runner
    # as the orchestrator in a route unit test intentionally exercises SINGLE.
    app.state.orchestrator = runner
    app.state.tracer = _StubTracer()


def _session_row(
    *,
    session_id: UUID | None = None,
    user_id: UUID = EMP_ID,
    title: str = "Test session",
    status: str = "active",
) -> dict[str, Any]:
    return {
        "id": session_id or uuid4(),
        "agent_id": AGENT_ID,
        "tenant_id": TID,
        "user_id": user_id,
        "title": title,
        "status": status,
        "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        "last_active_at": datetime(2026, 7, 1, 12, 30, tzinfo=UTC),
    }


def _message_row(
    *,
    session_id: UUID,
    role: str = "user",
    content: str = "Hello",
    event_type: str | None = None,
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "session_id": session_id,
        "tenant_id": TID,
        "role": role,
        "content": content,
        "event_type": event_type,
        "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    }


# ============================================================
# List sessions
# ============================================================


class TestListSessions:
    async def test_employee_lists_own_sessions(self) -> None:
        sid = uuid4()
        rows = [_session_row(session_id=sid, user_id=EMP_ID)]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=rows)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/sessions",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == str(sid)
        assert data[0]["title"] == "Test session"

    async def test_empty_list(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/sessions",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_no_token_returns_401(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/sessions")
        assert resp.status_code == 401

    async def test_limit_query_param_accepted(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/sessions?limit=10",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200


# ============================================================
# Get session detail
# ============================================================


class TestGetSession:
    async def test_get_existing_session(self) -> None:
        sid = uuid4()
        app = create_app(_config())
        db = _mock_db(single_row=_session_row(session_id=sid))
        app.state.db = db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/sessions/{sid}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(sid)
        assert data["agent_id"] == str(AGENT_ID)
        ownership_call = db.fetch_one.call_args
        assert "tenant_id = :p1" in ownership_call.args[0]
        assert "user_id = :p2" in ownership_call.args[0]
        assert ownership_call.args[1:] == (sid, TID, EMP_ID)

    async def test_admin_access_remains_tenant_scoped(self) -> None:
        sid = uuid4()
        app = create_app(_config())
        db = _mock_db(single_row=_session_row(session_id=sid))
        app.state.db = db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/sessions/{sid}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        ownership_call = db.fetch_one.call_args
        assert "tenant_id = :p1" in ownership_call.args[0]
        assert "user_id = :p2" not in ownership_call.args[0]
        assert ownership_call.args[1:] == (sid, TID)

    async def test_not_found_returns_404(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/sessions/{uuid4()}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 404

    async def test_other_user_session_returns_404(self) -> None:
        """Employee cannot see another employee's session (db returns None)."""
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/sessions/{uuid4()}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 404


# ============================================================
# List messages
# ============================================================


class TestListMessages:
    async def test_list_messages_for_session(self) -> None:
        sid = uuid4()
        session = _session_row(session_id=sid)
        msgs = [
            _message_row(session_id=sid, role="user", content="Hi"),
            _message_row(session_id=sid, role="assistant", content="Hello!", event_type="final"),
        ]
        app = create_app(_config())
        db = _mock_db(fetch_rows=msgs, single_row=session)
        app.state.db = db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/sessions/{sid}/messages",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["role"] == "user"
        assert data[1]["role"] == "assistant"
        assert data[1]["event_type"] == "final"
        message_call = db.fetch.call_args
        assert "session_id = :p0 AND tenant_id = :p1" in message_call.args[0]
        assert message_call.args[1:] == (sid, TID, 100)

    async def test_empty_messages(self) -> None:
        sid = uuid4()
        session = _session_row(session_id=sid)
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=[], single_row=session)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/sessions/{sid}/messages",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_session_not_found_returns_404(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/sessions/{uuid4()}/messages",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 404


# ============================================================
# Delete session
# ============================================================


class TestDeleteSession:
    async def test_delete_existing_session(self) -> None:
        sid = uuid4()
        session = _session_row(session_id=sid)
        app = create_app(_config())
        app.state.db = _mock_db(single_row=session)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(
                f"/sessions/{sid}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 204
        statements = [call.args[0] for call in app.state.db.execute.await_args_list]
        assert any("UPDATE harness.approvals" in sql for sql in statements)
        assert any("DELETE FROM checkpoint_writes" in sql for sql in statements)
        assert any("DELETE FROM checkpoint_blobs" in sql for sql in statements)
        assert any("DELETE FROM checkpoints" in sql for sql in statements)
        assert any("DELETE FROM agent.sessions" in sql for sql in statements)
        expected_thread_id = f"tenant:{TID}:agent:{AGENT_ID}:session:{sid}"
        checkpoint_calls = [
            call
            for call in app.state.db.execute.await_args_list
            if "checkpoint_" in call.args[0] or "DELETE FROM checkpoints" in call.args[0]
        ]
        assert len(checkpoint_calls) == 3
        assert all(call.args[1:] == (expected_thread_id,) for call in checkpoint_calls)
        approval_call = next(
            call
            for call in app.state.db.execute.await_args_list
            if "UPDATE harness.approvals" in call.args[0]
        )
        assert "status IN ('pending', 'approved')" in approval_call.args[0]
        assert approval_call.args[1:] == (sid, TID)

    async def test_delete_not_found_returns_404(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(
                f"/sessions/{uuid4()}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 404
        app.state.db.execute.assert_not_awaited()


# ============================================================
# Rename session
# ============================================================


class TestRenameSession:
    async def test_rename_session(self) -> None:
        sid = uuid4()
        session = _session_row(session_id=sid, title="Old title")
        app = create_app(_config())
        app.state.db = _mock_db(single_row=session)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.patch(
                f"/sessions/{sid}",
                json={"title": "New title"},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        # The mock returns the same row; in production the second fetch_one
        # would return the updated row. Here we verify the route succeeds.

    async def test_rename_not_found_returns_404(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.patch(
                f"/sessions/{uuid4()}",
                json={"title": "New title"},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 404


# ============================================================
# Invoke persistence (session + messages)
# ============================================================


class TestInvokePersistence:
    """Verify /invoke creates a session, persists user + assistant messages."""

    async def test_invoke_creates_session_and_persists_messages(self) -> None:
        from eaos.agent.runner import AgentEvent

        class _StubRunner:
            async def invoke(
                self,
                ctx: Any,
                message: str,
                *,
                attachments: list[Any] | None = None,
            ) -> Any:
                yield AgentEvent(type="token", content="Hello")
                yield AgentEvent(type="final", content="Hello world!", agent_id=ctx.agent_id)

        app = create_app(_config())
        db = _mock_db()
        app.state.db = db
        _wire_invoke_dependencies(app, _StubRunner())

        token = create_jwt_token(SECRET, EMP_ID, TID, "employee")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/invoke",
                json={"agent_id": str(AGENT_ID), "message": "Hi there"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        # X-Session-Id header should be set
        assert "x-session-id" in resp.headers
        # execute should have been called for: INSERT session, INSERT user msg,
        # UPDATE last_active, INSERT assistant msg, UPDATE last_active
        assert db.execute.await_count >= 3

    async def test_invoke_with_existing_session_id(self) -> None:
        from eaos.agent.runner import AgentEvent

        sid = uuid4()

        class _StubRunner:
            async def invoke(
                self,
                ctx: Any,
                message: str,
                *,
                attachments: list[Any] | None = None,
            ) -> Any:
                yield AgentEvent(type="final", content="Reply", agent_id=ctx.agent_id)

        app = create_app(_config())
        db = _mock_db(single_row={"id": sid})  # session exists
        app.state.db = db
        _wire_invoke_dependencies(app, _StubRunner())

        token = create_jwt_token(SECRET, EMP_ID, TID, "employee")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/invoke",
                json={"agent_id": str(AGENT_ID), "message": "Hi", "session_id": str(sid)},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.headers["x-session-id"] == str(sid)
        # fetch_one called to verify session ownership
        db.fetch_one.assert_awaited()

    async def test_invoke_with_nonexistent_session_returns_404(self) -> None:
        class _StubRunner:
            async def invoke(
                self,
                ctx: Any,
                message: str,
                *,
                attachments: list[Any] | None = None,
            ) -> Any:
                return
                yield  # make it an async generator

        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)  # session not found
        _wire_invoke_dependencies(app, _StubRunner())

        token = create_jwt_token(SECRET, EMP_ID, TID, "employee")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/invoke",
                json={"agent_id": str(AGENT_ID), "message": "Hi", "session_id": str(uuid4())},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 404
