"""Tests for SSE invoke, webhook, and interrupt resume routes."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.agent.runner import AgentEvent
from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.core.errors import PermissionDeniedError
from eaos.gateway.api.app import create_app
from eaos.infra.llm.router_impl import LLMUsageRecord
from httpx import ASGITransport, AsyncClient


def _config() -> AppConfig:
    return AppConfig(secret_key="test-secret", debug=True)  # type: ignore[call-arg]


def _token(config: AppConfig) -> str:
    return create_jwt_token(
        secret=config.secret_key,
        user_id=uuid4(),
        tenant_id=uuid4(),
        role="employee",
    )


def _mock_db() -> Any:
    """Build a mock DbClient — all ops are no-ops, fetch_one/fetch return None/empty."""
    db: Any = AsyncMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.fetch = AsyncMock(return_value=[])
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock(return_value=None)
    return db


class _StubRunner:
    """Minimal runner stub — yields pre-configured events."""

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = events
        self.last_ctx: Any = None
        self.last_message: str = ""
        self.last_approval: dict[str, Any] | None = None

    async def invoke(
        self,
        ctx: Any,
        user_message: str,
        *,
        attachments: list[Any] | None = None,
    ) -> Any:
        self.last_ctx = ctx
        self.last_message = user_message
        for event in self._events:
            yield event

    async def interrupt_and_resume(self, ctx: Any, approval: dict[str, Any]) -> Any:
        self.last_ctx = ctx
        self.last_approval = approval
        status = approval.get("status", "")
        if status == "rejected":
            raise PermissionDeniedError(f"approval {approval.get('id')} rejected")
        for event in self._events:
            yield event


class _StubSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}
        self.cost: tuple[int, float | None] | None = None

    def set_attribute(self, *args: Any, **kwargs: Any) -> None:
        self.attributes[str(args[0])] = args[1]

    def set_cost(self, tokens: int, usd: float | None = None) -> None:
        self.cost = (tokens, usd)


class _StubTracer:
    def __init__(self) -> None:
        self.handle = _StubSpan()

    async def span(self, *args: Any, **kwargs: Any) -> Any:
        yield self.handle


class _StubUsageRouter:
    @asynccontextmanager
    async def capture_usage(self) -> Any:
        yield [
            LLMUsageRecord(
                provider="test",
                model="test-model",
                task_type="plan",
                prompt_tokens=6,
                completion_tokens=4,
                total_tokens=10,
                latency_ms=12,
                success=True,
            )
        ]


def _parse_sse(text: str) -> list[str]:
    """Extract data payloads from SSE text."""
    payloads: list[str] = []
    for line in text.strip().split("\n\n"):
        line = line.strip()
        if line.startswith("data: "):
            payloads.append(line[6:])
    return payloads


class TestInvokeSSE:
    async def test_invoke_streams_events_as_sse(self) -> None:
        events = [
            AgentEvent(type="plan", content="step-1"),
            AgentEvent(type="final", content="Hello!"),
        ]
        runner = _StubRunner(events)
        config = _config()
        app = create_app(config)
        app.state.runner = runner
        app.state.orchestrator = runner
        app.state.db = _mock_db()
        app.state.tracer = _StubTracer()

        token = _token(config)
        body = {"agent_id": str(uuid4()), "message": "hi"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/invoke",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        payloads = _parse_sse(response.text)
        assert len(payloads) == 3  # 2 events + [DONE]

        first = json.loads(payloads[0])
        assert first["type"] == "plan"
        assert first["content"] == "step-1"

        second = json.loads(payloads[1])
        assert second["type"] == "final"
        assert second["content"] == "Hello!"

        assert payloads[2] == "[DONE]"

    async def test_invoke_passes_ctx_from_principal(self) -> None:
        runner = _StubRunner([])
        config = _config()
        app = create_app(config)
        app.state.runner = runner
        app.state.orchestrator = runner
        app.state.db = _mock_db()
        app.state.tracer = _StubTracer()

        uid = uuid4()
        tid = uuid4()
        token = create_jwt_token(
            secret=config.secret_key, user_id=uid, tenant_id=tid, role="employee"
        )
        agent_id = uuid4()
        body = {"agent_id": str(agent_id), "message": "hello"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/invoke",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert runner.last_ctx.tenant_id == tid
        assert runner.last_ctx.user_id == uid
        assert runner.last_ctx.agent_id == agent_id
        assert runner.last_message == "hello"

    async def test_invoke_loads_tenant_scoped_memberships(self) -> None:
        runner = _StubRunner([])
        config = _config()
        app = create_app(config)
        app.state.runner = runner
        app.state.orchestrator = runner
        db = _mock_db()
        department_id = uuid4()
        db.fetch.return_value = [{"department_id": department_id}]
        app.state.db = db
        app.state.tracer = _StubTracer()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/invoke",
                json={"agent_id": str(uuid4()), "message": "hello"},
                headers={"Authorization": f"Bearer {_token(config)}"},
            )

        assert response.status_code == 200
        assert runner.last_ctx.department_ids == [department_id]
        membership_sql = db.fetch.call_args.args[0]
        assert "iam.memberships" in membership_sql
        assert "JOIN iam.departments" in membership_sql
        assert "d.tenant_id = :p1" in membership_sql

    async def test_invoke_attaches_request_usage_to_outer_trace(self) -> None:
        runner = _StubRunner([AgentEvent(type="final", content="done")])
        tracer = _StubTracer()
        config = _config()
        app = create_app(config)
        app.state.runner = runner
        app.state.orchestrator = runner
        app.state.db = _mock_db()
        app.state.tracer = tracer
        app.state._deps = SimpleNamespace(llm=_StubUsageRouter())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/invoke",
                json={"agent_id": str(uuid4()), "message": "hello"},
                headers={"Authorization": f"Bearer {_token(config)}"},
            )

        assert response.status_code == 200
        assert tracer.handle.cost == (10, None)
        assert tracer.handle.attributes["llm_call_count"] == 1
        assert tracer.handle.attributes["llm_usage"][0]["model"] == "test-model"

    async def test_invoke_without_token_returns_401(self) -> None:
        app = create_app(_config())
        app.state.runner = _StubRunner([])
        app.state.db = _mock_db()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/invoke",
                json={"agent_id": str(uuid4()), "message": "hi"},
            )

        assert response.status_code == 401


class TestWebhook:
    async def test_webhook_returns_accepted(self) -> None:
        gateway = AsyncMock()
        gateway.handle_webhook.return_value = {
            "status": "accepted",
            "message_id": "msg-123",
        }
        app = create_app(_config())
        app.state.gateway = gateway

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhook/dingtalk",
                json={"text": "hello"},
                headers={"signature": "abc"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["message_id"] == "msg-123"

        gateway.handle_webhook.assert_awaited_once()
        call_args = gateway.handle_webhook.call_args
        assert call_args.args[0] == "dingtalk"
        assert call_args.args[1] == {"text": "hello"}

    async def test_webhook_unknown_channel_returns_404(self) -> None:
        gateway = AsyncMock()
        gateway.handle_webhook.return_value = {
            "status": "error",
            "code": 404,
            "message": "unknown channel: slack",
        }
        app = create_app(_config())
        app.state.gateway = gateway

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/webhook/slack", json={})

        assert response.status_code == 404
        assert "unknown channel" in response.json()["detail"]

    async def test_webhook_invalid_signature_returns_401(self) -> None:
        gateway = AsyncMock()
        gateway.handle_webhook.return_value = {
            "status": "error",
            "code": 401,
            "message": "invalid signature",
        }
        app = create_app(_config())
        app.state.gateway = gateway

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/webhook/dingtalk", json={})

        assert response.status_code == 401


class TestInterruptResume:
    async def test_resume_approved_streams_events(self) -> None:
        events = [
            AgentEvent(type="token", content="resumed"),
            AgentEvent(type="final", content="done"),
        ]
        runner = _StubRunner(events)
        config = _config()
        app = create_app(config)
        app.state.runner = runner
        db = _mock_db()
        app.state.db = db
        tracer = _StubTracer()
        app.state.tracer = tracer
        app.state._deps = SimpleNamespace(llm=_StubUsageRouter())

        user_id = uuid4()
        tenant_id = uuid4()
        token = create_jwt_token(
            secret=config.secret_key,
            user_id=user_id,
            tenant_id=tenant_id,
            role="employee",
        )
        session_id = uuid4()
        agent_id = uuid4()
        approval_id = uuid4()
        db.fetch_one.return_value = {
            "id": approval_id,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "status": "approved",
            "agent_id": agent_id,
            "skill_id": None,
            "requested_by": user_id,
            "tool_name": "erp_create_sales_order",
            "resource": "orders",
            "operation": "create",
            "risk_level": "high",
            "intent_data": {"data": {}, "idempotency_key": "test"},
        }
        body = {
            "agent_id": str(agent_id),
            "approval_id": str(approval_id),
            "decision": "approved",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/interrupt/{session_id}/resume",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        payloads = _parse_sse(response.text)
        assert len(payloads) == 3  # 2 events + [DONE]

        first = json.loads(payloads[0])
        assert first["type"] == "token"
        assert first["content"] == "resumed"

        assert payloads[2] == "[DONE]"

        assert runner.last_ctx.session_id == session_id
        assert runner.last_approval is not None
        assert runner.last_approval["status"] == "approved"
        assert tracer.handle.cost == (10, None)

    async def test_resume_rejected_yields_error_event(self) -> None:
        runner = _StubRunner([])
        config = _config()
        app = create_app(config)
        app.state.runner = runner
        db = _mock_db()
        app.state.db = db
        app.state.tracer = _StubTracer()

        user_id = uuid4()
        tenant_id = uuid4()
        session_id = uuid4()
        agent_id = uuid4()
        approval_id = uuid4()
        token = create_jwt_token(
            secret=config.secret_key,
            user_id=user_id,
            tenant_id=tenant_id,
            role="employee",
        )
        db.fetch_one.return_value = {
            "id": approval_id,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "status": "rejected",
            "agent_id": agent_id,
            "skill_id": None,
            "requested_by": user_id,
            "tool_name": "erp_create_sales_order",
            "resource": "orders",
            "operation": "create",
            "risk_level": "high",
            "intent_data": {"data": {}, "idempotency_key": "test"},
        }
        body = {
            "agent_id": str(agent_id),
            "approval_id": str(approval_id),
            "decision": "rejected",
            "reason": "too risky",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/interrupt/{session_id}/resume",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 403
        assert "rejected" in response.json()["detail"]

    async def test_resume_without_token_returns_401(self) -> None:
        app = create_app(_config())
        app.state.runner = _StubRunner([])
        app.state.db = _mock_db()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/interrupt/{uuid4()}/resume",
                json={
                    "agent_id": str(uuid4()),
                    "approval_id": str(uuid4()),
                    "decision": "approved",
                },
            )

        assert response.status_code == 401
