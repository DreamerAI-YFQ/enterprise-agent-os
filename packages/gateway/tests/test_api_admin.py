"""Tests for admin/governance API routes — auth, triggers, policies, approvals, spans."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:
    from uuid import UUID


def _config() -> AppConfig:
    return AppConfig(secret_key="test-secret", debug=True)  # type: ignore[call-arg]


def _token(
    config: AppConfig,
    *,
    role: str = "admin",
    tenant_id: UUID | None = None,
) -> str:
    return create_jwt_token(
        secret=config.secret_key,
        user_id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        role=role,
    )


class TestAdminAuth:
    async def test_non_admin_returns_403(self) -> None:
        config = _config()
        app = create_app(config)
        token = _token(config, role="employee")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/triggers", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 403

    async def test_not_configured_returns_501(self) -> None:
        config = _config()
        app = create_app(config)
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/triggers", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 501


class TestTriggers:
    async def test_list_triggers(self) -> None:
        from eaos.agent.ambient import AmbientTrigger, TriggerConfig

        config = _config()
        tid = uuid4()
        app = create_app(config)
        monitor = AsyncMock()
        monitor.list_triggers.return_value = [
            TriggerConfig(
                trigger_type=AmbientTrigger.THRESHOLD,
                agent_id=uuid4(),
                condition={"metric": "inventory", "op": "<", "value": 100},
                notify_channel="slack",
                interval_sec=300,
            )
        ]
        app.state.ambient_monitor = monitor
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/triggers", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["trigger_type"] == "threshold"
        assert data[0]["notify_channel"] == "slack"
        monitor.list_triggers.assert_awaited_once()

    async def test_create_trigger(self) -> None:
        config = _config()
        tid = uuid4()
        aid = uuid4()
        trigger_id = uuid4()
        app = create_app(config)
        monitor = AsyncMock()
        monitor.register_trigger.return_value = trigger_id
        app.state.ambient_monitor = monitor
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/triggers",
                json={
                    "agent_id": str(aid),
                    "trigger_type": "threshold",
                    "condition": {"metric": "inventory", "op": "<", "value": 100},
                    "notify_channel": "slack",
                    "interval_sec": 600,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 201
        assert response.json()["trigger_id"] == str(trigger_id)
        monitor.register_trigger.assert_awaited_once()

    async def test_create_trigger_invalid_type(self) -> None:
        config = _config()
        app = create_app(config)
        app.state.ambient_monitor = AsyncMock()
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/triggers",
                json={
                    "agent_id": str(uuid4()),
                    "trigger_type": "unknown",
                    "condition": {},
                    "notify_channel": "slack",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 422

    async def test_delete_trigger(self) -> None:
        config = _config()
        trigger_id = uuid4()
        app = create_app(config)
        monitor = AsyncMock()
        monitor.unregister_trigger.return_value = None
        app.state.ambient_monitor = monitor
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(
                f"/admin/triggers/{trigger_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 204
        monitor.unregister_trigger.assert_awaited_once()


class TestPolicies:
    async def test_list_policies(self) -> None:
        from eaos.harness.policy import Policy, PolicyStatus

        config = _config()
        tid = uuid4()
        app = create_app(config)
        engine = AsyncMock()
        engine.list_versions.return_value = [
            Policy(
                name="capability.personal_agent",
                version="1.0.0",
                content={"max_iterations": 10},
                status=PolicyStatus.ACTIVE,
                tenant_id=tid,
            )
        ]
        app.state.policy_engine = engine
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/policies?name=capability.personal_agent",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "capability.personal_agent"
        assert data[0]["status"] == "active"

    async def test_publish_policy(self) -> None:
        config = _config()
        app = create_app(config)
        engine = AsyncMock()
        engine.publish.return_value = None
        app.state.policy_engine = engine
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/policies",
                json={
                    "name": "capability.personal_agent",
                    "version": "2.0.0",
                    "content": {"max_iterations": 20},
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 201
        assert response.json()["version"] == "2.0.0"
        engine.publish.assert_awaited_once()

    async def test_activate_policy(self) -> None:
        config = _config()
        app = create_app(config)
        engine = AsyncMock()
        engine.activate.return_value = None
        app.state.policy_engine = engine
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/policies/capability.personal_agent/activate?version=2.0.0",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "active"
        engine.activate.assert_awaited_once()

    async def test_rollback_policy(self) -> None:
        config = _config()
        app = create_app(config)
        engine = AsyncMock()
        engine.rollback.return_value = None
        app.state.policy_engine = engine
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/policies/capability.personal_agent/rollback?version=1.0.0",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "rollback"
        engine.rollback.assert_awaited_once()


class TestApprovals:
    async def test_list_approvals(self) -> None:
        from eaos.harness.evolution.approval import ApprovalRequest

        config = _config()
        tid = uuid4()
        app = create_app(config)
        gate = AsyncMock()
        gate.list_pending.return_value = [
            ApprovalRequest(
                id=uuid4(),
                tenant_id=tid,
                agent_id=uuid4(),
                skill_id=uuid4(),
                session_id=uuid4(),
                reason="high_risk",
                status="pending",
                requested_by=uuid4(),
                decided_by=None,
                decided_at=None,
                created_at=datetime.now(UTC),
            )
        ]
        app.state.approval_gate = gate
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/approvals", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "pending"

    async def test_approve(self) -> None:
        config = _config()
        approval_id = uuid4()
        app = create_app(config)
        gate = AsyncMock()
        gate.approve.return_value = None
        app.state.approval_gate = gate
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/admin/approvals/{approval_id}/approve",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"
        gate.approve.assert_awaited_once()

    async def test_reject(self) -> None:
        config = _config()
        approval_id = uuid4()
        app = create_app(config)
        gate = AsyncMock()
        gate.reject.return_value = None
        app.state.approval_gate = gate
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/admin/approvals/{approval_id}/reject",
                json={"reason": "too risky"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        gate.reject.assert_awaited_once()


class TestSpans:
    async def test_overview(self) -> None:
        from eaos.observability.query import Overview

        config = _config()
        tid = uuid4()
        app = create_app(config)
        trace_query = AsyncMock()
        trace_query.overview.return_value = Overview(
            tenant_id=tid,
            total_agents=5,
            active_users_today=10,
            total_tokens_today=50000,
            total_cost_usd_today=12.5,
            top_skills=[{"name": "rag", "count": 20}],
            task_success_rate=0.95,
        )
        app.state.trace_query = trace_query
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/spans/overview?start=2024-01-01T00:00:00&end=2024-12-31T23:59:59",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["total_agents"] == 5
        assert data["total_tokens_today"] == 50000
        assert data["task_success_rate"] == 0.95
        assert len(data["top_skills"]) == 1

    async def test_trace_detail(self) -> None:
        from eaos.observability.span import Granularity, Span

        config = _config()
        app = create_app(config)
        trace_query = AsyncMock()
        trace_query.trace_detail.return_value = [
            Span(
                id=uuid4(),
                tenant_id=uuid4(),
                trace_id=uuid4(),
                parent_span_id=None,
                agent_id=uuid4(),
                session_id=None,
                granularity=Granularity.CALL,
                name="test.span",
                start_time=datetime.now(UTC),
                end_time=None,
                duration_ms=None,
                status="running",
                attributes={},
                events=[],
                cost_tokens=0,
                cost_usd=0.0,
                user_id=None,
            )
        ]
        app.state.trace_query = trace_query
        token = _token(config)
        trace_id = uuid4()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/admin/spans/trace/{trace_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "test.span"


class TestProtocolStubs:
    async def test_audit_logs_returns_501(self) -> None:
        config = _config()
        app = create_app(config)
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/audit-logs", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 501

    async def test_quota_update_returns_501(self) -> None:
        config = _config()
        app = create_app(config)
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/admin/quotas", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 501

    async def test_get_quota(self) -> None:
        from eaos.harness.cost.governor import QuotaConfig, QuotaScope, QuotaStatus

        config = _config()
        tid = uuid4()
        app = create_app(config)
        governor = AsyncMock()
        governor.get_status.return_value = QuotaStatus(
            config=QuotaConfig(
                tenant_id=tid,
                scope=QuotaScope.ORGANIZATION,
                owner_id=None,
                period="monthly",
                token_limit=100000,
            ),
            token_used=30000,
            cost_used_usd=5.0,
            remaining_tokens=70000,
            remaining_cost_usd=None,
            utilization_pct=30.0,
        )
        app.state.cost_governor = governor
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/quotas?scope=organization",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["token_used"] == 30000
        assert data["utilization_pct"] == 30.0
