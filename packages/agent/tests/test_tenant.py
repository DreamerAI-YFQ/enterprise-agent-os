"""Tests for PgTenantManager — thread_id resolution + tenant creation."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from eaos.agent.dispatcher import AgentConfig, AgentScope, CapabilityBoundary
from eaos.agent.tenant import PgTenantManager


def _make_config(agent_id: UUID, tenant_id: UUID, scope: AgentScope) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        tenant_id=tenant_id,
        scope=scope,
        owner_id=uuid4(),
        name="test-agent",
        description=None,
        model_config={},
        capability=CapabilityBoundary(),
    )


class TestResolveThreadId:
    @pytest.fixture
    def manager(self) -> PgTenantManager:
        db = AsyncMock()
        dispatcher = AsyncMock()
        return PgTenantManager(db, dispatcher)

    async def test_personal_with_session(self, manager: PgTenantManager) -> None:
        tenant_id = uuid4()
        agent_id = uuid4()
        session_id = uuid4()
        thread_id = await manager.resolve_thread_id(
            tenant_id, agent_id, session_id, AgentScope.PERSONAL
        )
        assert thread_id == f"tenant:{tenant_id}:agent:{agent_id}:session:{session_id}"

    async def test_personal_without_session_uses_fresh_fallback(
        self, manager: PgTenantManager
    ) -> None:
        tenant_id = uuid4()
        agent_id = uuid4()
        first = await manager.resolve_thread_id(tenant_id, agent_id, None, AgentScope.PERSONAL)
        second = await manager.resolve_thread_id(tenant_id, agent_id, None, AgentScope.PERSONAL)
        prefix = f"tenant:{tenant_id}:agent:{agent_id}:session:"
        assert first.startswith(prefix)
        assert second.startswith(prefix)
        assert first != second

    async def test_department_keeps_session_isolation(self, manager: PgTenantManager) -> None:
        tenant_id = uuid4()
        agent_id = uuid4()
        session_id = uuid4()
        thread_id = await manager.resolve_thread_id(
            tenant_id, agent_id, session_id, AgentScope.DEPARTMENT
        )
        assert thread_id == f"tenant:{tenant_id}:agent:{agent_id}:session:{session_id}"

    async def test_company_without_session_uses_fresh_fallback(
        self, manager: PgTenantManager
    ) -> None:
        tenant_id = uuid4()
        agent_id = uuid4()
        first = await manager.resolve_thread_id(tenant_id, agent_id, None, AgentScope.COMPANY)
        second = await manager.resolve_thread_id(tenant_id, agent_id, None, AgentScope.COMPANY)
        assert first != second

    async def test_department_sessions_do_not_share_checkpoint(
        self, manager: PgTenantManager
    ) -> None:
        tenant_id = uuid4()
        agent_id = uuid4()
        first = await manager.resolve_thread_id(tenant_id, agent_id, uuid4(), AgentScope.DEPARTMENT)
        second = await manager.resolve_thread_id(
            tenant_id, agent_id, uuid4(), AgentScope.DEPARTMENT
        )
        assert first != second


class TestCreateTenant:
    async def test_create_tenant_returns_id(self) -> None:
        tenant_id = uuid4()
        db = AsyncMock()
        db.fetch.return_value = [{"id": tenant_id}]
        dispatcher = AsyncMock()
        manager = PgTenantManager(db, dispatcher)

        result = await manager.create_tenant("Acme Corp")

        assert result == tenant_id
        db.fetch.assert_awaited_once()
        sql_arg = db.fetch.call_args.args[0]
        assert "INSERT INTO iam.tenants" in sql_arg
        assert "RETURNING id" in sql_arg

    async def test_create_tenant_slug_contains_name(self) -> None:
        tenant_id = uuid4()
        db = AsyncMock()
        db.fetch.return_value = [{"id": tenant_id}]
        dispatcher = AsyncMock()
        manager = PgTenantManager(db, dispatcher)

        await manager.create_tenant("Acme Corp")

        positional_args = db.fetch.call_args.args[1:]
        name_arg, slug_arg, settings_arg = positional_args
        assert name_arg == "Acme Corp"
        assert slug_arg.startswith("acme-corp")
        assert settings_arg == "{}"


class TestCreateAgentInTenant:
    async def test_delegates_to_dispatcher(self) -> None:
        tenant_id = uuid4()
        agent_id = uuid4()
        owner_id = uuid4()
        expected = _make_config(agent_id, tenant_id, AgentScope.PERSONAL)
        db = AsyncMock()
        dispatcher = AsyncMock()
        dispatcher.create_agent.return_value = expected
        manager = PgTenantManager(db, dispatcher)

        creator = uuid4()
        result = await manager.create_agent_in_tenant(
            tenant_id, AgentScope.PERSONAL, owner_id, "agent-name", creator
        )

        assert result is expected
        dispatcher.create_agent.assert_awaited_once_with(
            tenant_id, AgentScope.PERSONAL, owner_id, "agent-name", creator
        )
