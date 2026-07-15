"""Tests for PgAgentDispatcher — scope visibility + CRUD."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from eaos.agent.dispatcher import (
    AgentConfig,
    AgentScope,
    CapabilityBoundary,
    PgAgentDispatcher,
)
from eaos.core.context import TenantContext
from eaos.core.errors import NotFoundError, PermissionDeniedError


def _row(
    *,
    agent_id: UUID | None = None,
    tenant_id: UUID | None = None,
    scope: AgentScope = AgentScope.PERSONAL,
    owner_id: UUID | None = None,
    capability: dict[str, object] | str | None = None,
) -> dict[str, object]:
    return {
        "id": agent_id or uuid4(),
        "tenant_id": tenant_id or uuid4(),
        "scope": str(scope.value),
        "owner_id": owner_id,
        "name": "agent-x",
        "description": None,
        "model_config": {},
        "capability": capability or {},
        "status": "active",
    }


def _ctx(tenant_id: UUID, user_id: UUID, depts: list[UUID] | None = None) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=uuid4(),
        agent_scope="personal",
        department_ids=depts or [],
    )


class TestCreateAgent:
    async def test_create_returns_config(self) -> None:
        tenant_id = uuid4()
        agent_id = uuid4()
        db = AsyncMock()
        db.fetch.return_value = [_row(agent_id=agent_id, tenant_id=tenant_id)]
        dispatcher = PgAgentDispatcher(db)

        result = await dispatcher.create_agent(
            tenant_id, AgentScope.PERSONAL, uuid4(), "name", uuid4()
        )

        assert result.id == agent_id
        assert result.tenant_id == tenant_id
        sql_arg = db.fetch.call_args.args[0]
        assert "INSERT INTO agent.agents" in sql_arg
        assert "CAST(:p5 AS jsonb)" in sql_arg

    async def test_create_serializes_capability(self) -> None:
        tenant_id = uuid4()
        db = AsyncMock()
        db.fetch.return_value = [_row(tenant_id=tenant_id)]
        dispatcher = PgAgentDispatcher(db)

        await dispatcher.create_agent(
            tenant_id, AgentScope.COMPANY, None, "name", uuid4()
        )

        positional = db.fetch.call_args.args[1:]
        capability_json = positional[-1]
        parsed = json.loads(capability_json)
        assert parsed["max_task_duration_sec"] == 600
        assert parsed["max_iterations"] == 10


class TestGet:
    async def test_get_returns_config(self) -> None:
        tenant_id = uuid4()
        agent_id = uuid4()
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [
            _row(agent_id=agent_id, tenant_id=tenant_id)
        ]
        dispatcher = PgAgentDispatcher(db)

        result = await dispatcher.get(agent_id, tenant_id)
        assert result.id == agent_id

    async def test_get_missing_raises(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = []
        dispatcher = PgAgentDispatcher(db)

        with pytest.raises(NotFoundError):
            await dispatcher.get(uuid4(), uuid4())


class TestResolveAgentForUser:
    async def test_personal_owner_visible(self) -> None:
        tenant_id = uuid4()
        user_id = uuid4()
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [
            _row(tenant_id=tenant_id, scope=AgentScope.PERSONAL, owner_id=user_id)
        ]
        dispatcher = PgAgentDispatcher(db)

        result = await dispatcher.resolve_agent_for_user(tenant_id, user_id, uuid4())
        assert result.scope == AgentScope.PERSONAL

    async def test_personal_other_user_denied(self) -> None:
        tenant_id = uuid4()
        owner = uuid4()
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [
            _row(tenant_id=tenant_id, scope=AgentScope.PERSONAL, owner_id=owner)
        ]
        dispatcher = PgAgentDispatcher(db)

        with pytest.raises(PermissionDeniedError):
            await dispatcher.resolve_agent_for_user(tenant_id, uuid4(), uuid4())

    async def test_department_member_visible(self) -> None:
        tenant_id = uuid4()
        dept_id = uuid4()
        user_id = uuid4()
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [
            _row(tenant_id=tenant_id, scope=AgentScope.DEPARTMENT, owner_id=dept_id)
        ]
        db.fetch.return_value = [{"?column?": 1}]
        dispatcher = PgAgentDispatcher(db)

        result = await dispatcher.resolve_agent_for_user(tenant_id, user_id, uuid4())
        assert result.scope == AgentScope.DEPARTMENT

    async def test_department_non_member_denied(self) -> None:
        tenant_id = uuid4()
        dept_id = uuid4()
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [
            _row(tenant_id=tenant_id, scope=AgentScope.DEPARTMENT, owner_id=dept_id)
        ]
        db.fetch.return_value = []
        dispatcher = PgAgentDispatcher(db)

        with pytest.raises(PermissionDeniedError):
            await dispatcher.resolve_agent_for_user(tenant_id, uuid4(), uuid4())

    async def test_company_visible_to_all(self) -> None:
        tenant_id = uuid4()
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [
            _row(tenant_id=tenant_id, scope=AgentScope.COMPANY, owner_id=None)
        ]
        dispatcher = PgAgentDispatcher(db)

        result = await dispatcher.resolve_agent_for_user(tenant_id, uuid4(), uuid4())
        assert result.scope == AgentScope.COMPANY


class TestListAvailable:
    async def test_list_includes_personal_and_company(self) -> None:
        tenant_id = uuid4()
        user_id = uuid4()
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [
            _row(tenant_id=tenant_id, scope=AgentScope.PERSONAL, owner_id=user_id),
            _row(tenant_id=tenant_id, scope=AgentScope.COMPANY, owner_id=None),
        ]
        dispatcher = PgAgentDispatcher(db)
        ctx = _ctx(tenant_id, user_id)

        results = await dispatcher.list_available(ctx)

        assert len(results) == 2
        sql_arg = db.tenant_scoped_fetch.call_args.args[0]
        assert "scope = 'personal'" in sql_arg
        assert "scope = 'company'" in sql_arg

    async def test_list_with_departments(self) -> None:
        tenant_id = uuid4()
        user_id = uuid4()
        dept_id = uuid4()
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = []
        dispatcher = PgAgentDispatcher(db)
        ctx = _ctx(tenant_id, user_id, depts=[dept_id])

        await dispatcher.list_available(ctx)

        sql_arg = db.tenant_scoped_fetch.call_args.args[0]
        assert "scope = 'department'" in sql_arg
        assert "owner_id IN" in sql_arg


class TestAssignSkill:
    async def test_assign_skill_inserts(self) -> None:
        db = AsyncMock()
        dispatcher = PgAgentDispatcher(db)

        await dispatcher.assign_skill(uuid4(), uuid4(), uuid4())

        sql_arg = db.execute.call_args.args[0]
        assert "INSERT INTO agent.agent_skills" in sql_arg
        assert "ON CONFLICT DO NOTHING" in sql_arg


class TestUpdateCapability:
    async def test_update_returns_config(self) -> None:
        tenant_id = uuid4()
        agent_id = uuid4()
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [
            _row(agent_id=agent_id, tenant_id=tenant_id)
        ]
        dispatcher = PgAgentDispatcher(db)

        boundary = CapabilityBoundary(max_iterations=5)
        result = await dispatcher.update_capability(agent_id, tenant_id, boundary)

        assert result.id == agent_id
        # tenant_scoped_fetch(sql, tenant_id, capability_json, agent_id)
        positional = db.tenant_scoped_fetch.call_args.args[1:]
        capability_json = positional[1]
        parsed = json.loads(capability_json)
        assert parsed["max_iterations"] == 5

    async def test_update_missing_raises(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = []
        dispatcher = PgAgentDispatcher(db)

        with pytest.raises(NotFoundError):
            await dispatcher.update_capability(uuid4(), uuid4(), CapabilityBoundary())


class TestRowToConfig:
    def test_parses_capability_with_uuids(self) -> None:
        ds_id = uuid4()
        row = _row(
            capability={
                "allowed_datasources": [str(ds_id)],
                "allowed_skill_categories": ["data_analysis"],
                "max_iterations": 7,
            }
        )
        from eaos.agent.dispatcher import _row_to_config

        config = _row_to_config(row)
        assert isinstance(config, AgentConfig)
        assert config.capability.max_iterations == 7
        assert config.capability.allowed_datasources == [ds_id]

    def test_capability_string_parsed(self) -> None:
        row = _row(capability=json.dumps({"max_iterations": 3}))
        from eaos.agent.dispatcher import _row_to_config

        config = _row_to_config(row)
        assert config.capability.max_iterations == 3
