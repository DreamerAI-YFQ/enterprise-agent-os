"""Tests for /agents and /admin/agents API routes.

Uses a mock AgentDispatcher (AsyncMock) to verify list, detail, create,
update, and permission checks without a live DB.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

from eaos.agent.dispatcher import AgentScope, CapabilityBoundary
from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "agents-test-secret-32bytes!"
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


def _capability() -> CapabilityBoundary:
    return CapabilityBoundary(
        allowed_models=["gpt-4o"],
        allowed_datasources=[],
        writable_datasources=[],
        allowed_skill_categories=[],
        max_task_duration_sec=600,
        max_iterations=10,
    )


def _agent_config(
    *,
    agent_id: UUID = AGENT_ID,
    scope: AgentScope = AgentScope.PERSONAL,
    name: str = "test-agent",
) -> Any:
    """Build a mock AgentConfig-like object."""
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _Agent:
        id: UUID
        tenant_id: UUID
        scope: AgentScope
        owner_id: UUID | None
        name: str
        description: str | None
        model_config: dict[str, Any]
        capability: CapabilityBoundary
        assigned_skills: list[UUID]
        status: str

    return _Agent(
        id=agent_id,
        tenant_id=TID,
        scope=scope,
        owner_id=EMP_ID,
        name=name,
        description="test agent",
        model_config={"model": "gpt-4o"},
        capability=_capability(),
        assigned_skills=[],
        status="active",
    )


def _mock_dispatcher(
    *,
    agents: list[Any] | None = None,
    single_agent: Any | None = None,
    created_agent: Any | None = None,
) -> Any:
    """Build a mock AgentDispatcher."""
    d: Any = AsyncMock()
    d.list_available = AsyncMock(return_value=agents or [])
    d.resolve_agent_for_user = AsyncMock(return_value=single_agent or _agent_config())
    d.get = AsyncMock(return_value=single_agent or _agent_config())
    d.create_agent = AsyncMock(return_value=created_agent or _agent_config(name="new-agent"))
    d.update_capability = AsyncMock(return_value=single_agent or _agent_config())
    return d


class TestListAgents:
    async def test_list_returns_agents(self) -> None:
        app = create_app(_config())
        app.state.dispatcher = _mock_dispatcher(agents=[_agent_config(), _agent_config(name="a2")])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/agents",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "test-agent"
        assert data[0]["scope"] == "personal"
        assert "capability" in data[0]

    async def test_list_empty(self) -> None:
        app = create_app(_config())
        app.state.dispatcher = _mock_dispatcher(agents=[])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/agents",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_without_auth(self) -> None:
        app = create_app(_config())
        app.state.dispatcher = _mock_dispatcher()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/agents")
        assert resp.status_code == 401


class TestGetAgent:
    async def test_get_success(self) -> None:
        app = create_app(_config())
        app.state.dispatcher = _mock_dispatcher(single_agent=_agent_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/agents/{AGENT_ID}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json()["id"] == str(AGENT_ID)

    async def test_get_not_found(self) -> None:
        app = create_app(_config())
        d = _mock_dispatcher()
        d.resolve_agent_for_user = AsyncMock(side_effect=ValueError("not found"))
        app.state.dispatcher = d
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/agents/{AGENT_ID}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 404


class TestCreateAgent:
    async def test_create_success(self) -> None:
        app = create_app(_config())
        app.state.dispatcher = _mock_dispatcher()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/agents",
                json={"name": "new-agent", "scope": "personal"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 201
        assert "id" in resp.json()

    async def test_create_invalid_scope(self) -> None:
        app = create_app(_config())
        app.state.dispatcher = _mock_dispatcher()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/agents",
                json={"name": "bad", "scope": "invalid"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 422

    async def test_create_employee_forbidden(self) -> None:
        app = create_app(_config())
        app.state.dispatcher = _mock_dispatcher()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/agents",
                json={"name": "x", "scope": "personal"},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403


class TestUpdateAgent:
    async def test_update_capability(self) -> None:
        app = create_app(_config())
        base_agent = _agent_config()

        async def _update(
            agent_id: UUID, tenant_id: UUID, boundary: CapabilityBoundary
        ) -> Any:
            from dataclasses import dataclass

            @dataclass(frozen=True)
            class _Agent:
                id: UUID
                tenant_id: UUID
                scope: AgentScope
                owner_id: UUID | None
                name: str
                description: str | None
                model_config: dict[str, Any]
                capability: CapabilityBoundary
                assigned_skills: list[UUID]
                status: str

            return _Agent(
                id=base_agent.id,
                tenant_id=base_agent.tenant_id,
                scope=base_agent.scope,
                owner_id=base_agent.owner_id,
                name=base_agent.name,
                description=base_agent.description,
                model_config=dict(base_agent.model_config),
                capability=boundary,
                assigned_skills=list(base_agent.assigned_skills),
                status=base_agent.status,
            )

        d = _mock_dispatcher(single_agent=base_agent)
        d.update_capability = AsyncMock(side_effect=_update)
        app.state.dispatcher = d
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/admin/agents/{AGENT_ID}",
                json={"max_iterations": 20, "allowed_models": ["claude-3"]},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["capability"]["max_iterations"] == 20
        assert data["capability"]["allowed_models"] == ["claude-3"]

    async def test_update_not_found(self) -> None:
        app = create_app(_config())
        d = _mock_dispatcher()
        d.get = AsyncMock(side_effect=ValueError("not found"))
        app.state.dispatcher = d
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/admin/agents/{AGENT_ID}",
                json={"max_iterations": 5},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 404
