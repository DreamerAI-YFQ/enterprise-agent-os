"""Tests for /knowledge/search, /skills, and /memory API routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.core.errors import NotFoundError
from eaos.gateway.api.app import create_app
from eaos.skills.spec import RiskLevel, SkillCategory, SkillScope, SkillSpec
from httpx import ASGITransport, AsyncClient

SECRET = "f0-t6-t8-t11-secret-32byte!"
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


def _skill_spec(
    *,
    skill_id: UUID | None = None,
    scope: SkillScope = SkillScope.PERSONAL,
    owner_id: UUID | None = EMP_ID,
    name: str = "test-skill",
    status: str = "draft",
) -> SkillSpec:
    return SkillSpec(
        id=skill_id or uuid4(),
        tenant_id=TID,
        scope=scope,
        owner_id=owner_id,
        name=name,
        display_name=name.replace("-", " ").title(),
        description="A test skill",
        category=SkillCategory.KNOWLEDGE_API,
        risk_level=RiskLevel.LOW,
        instructions="Use when user asks about APIs",
        tools=[],
        status=status,
    )


# ============================================================
# Knowledge search
# ============================================================


class TestKnowledgeSearch:
    async def test_search_returns_results(self) -> None:
        from eaos.knowledge.engine import SearchResult

        results = [
            SearchResult(
                content="ERP API docs",
                score=0.95,
                source="rag",
                metadata={"doc": "erp_manual"},
            ),
        ]
        engine: Any = AsyncMock()
        engine.search = AsyncMock(return_value=results)

        app = create_app(_config())
        app.state.knowledge_engine = engine
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/knowledge/search",
                json={"query": "how to use ERP API"},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["content"] == "ERP API docs"
        assert data[0]["source"] == "rag"

    async def test_search_empty_results(self) -> None:
        engine: Any = AsyncMock()
        engine.search = AsyncMock(return_value=[])

        app = create_app(_config())
        app.state.knowledge_engine = engine
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/knowledge/search",
                json={"query": "nothing matches"},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_search_no_token_returns_401(self) -> None:
        app = create_app(_config())
        app.state.knowledge_engine = AsyncMock()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/knowledge/search",
                json={"query": "test"},
            )
        assert resp.status_code == 401


# ============================================================
# Skills CRUD
# ============================================================


class TestSkillsList:
    async def test_list_my_skills(self) -> None:
        skills = [_skill_spec(name="skill-1"), _skill_spec(name="skill-2")]
        registry: Any = AsyncMock()
        registry.list_by_tenant = AsyncMock(return_value=skills)

        app = create_app(_config())
        app.state.skill_registry = registry
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/skills",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "skill-1"

    async def test_list_empty(self) -> None:
        registry: Any = AsyncMock()
        registry.list_by_tenant = AsyncMock(return_value=[])

        app = create_app(_config())
        app.state.skill_registry = registry
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/skills",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []


class TestSkillsGet:
    async def test_get_skill_success(self) -> None:
        sid = uuid4()
        registry: Any = AsyncMock()
        registry.get = AsyncMock(return_value=_skill_spec(skill_id=sid))

        app = create_app(_config())
        app.state.skill_registry = registry
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/skills/{sid}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json()["id"] == str(sid)

    async def test_get_not_found(self) -> None:
        registry: Any = AsyncMock()
        registry.get = AsyncMock(side_effect=NotFoundError("not found"))

        app = create_app(_config())
        app.state.skill_registry = registry
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/skills/{uuid4()}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 404


class TestSkillsCreate:
    async def test_create_skill_success(self) -> None:
        sid = uuid4()
        registry: Any = AsyncMock()
        registry.create = AsyncMock(return_value=_skill_spec(skill_id=sid))

        app = create_app(_config())
        app.state.skill_registry = registry
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/skills",
                json={
                    "name": "my-skill",
                    "display_name": "My Skill",
                    "description": "Does things",
                    "category": "KNOWLEDGE_API",
                },
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 201
        assert resp.json()["id"] == str(sid)

    async def test_create_invalid_category(self) -> None:
        registry: Any = AsyncMock()
        app = create_app(_config())
        app.state.skill_registry = registry
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/skills",
                json={
                    "name": "bad",
                    "display_name": "Bad",
                    "description": "x",
                    "category": "INVALID",
                },
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 422


class TestSkillsPublish:
    async def test_publish_owner_success(self) -> None:
        sid = uuid4()
        registry: Any = AsyncMock()
        registry.get = AsyncMock(return_value=_skill_spec(skill_id=sid, owner_id=EMP_ID))
        registry.publish = AsyncMock(return_value=None)

        app = create_app(_config())
        app.state.skill_registry = registry
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/skills/{sid}/publish",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "published"

    async def test_publish_non_owner_forbidden(self) -> None:
        sid = uuid4()
        other_id = uuid4()
        registry: Any = AsyncMock()
        registry.get = AsyncMock(return_value=_skill_spec(skill_id=sid, owner_id=other_id))

        app = create_app(_config())
        app.state.skill_registry = registry
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/skills/{sid}/publish",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403


class TestAdminSkills:
    async def test_admin_list_all(self) -> None:
        skills = [_skill_spec(scope=SkillScope.COMPANY, owner_id=None)]
        registry: Any = AsyncMock()
        registry.list_by_tenant = AsyncMock(return_value=skills)

        app = create_app(_config())
        app.state.skill_registry = registry
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/skills",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_employee_cannot_access_admin(self) -> None:
        app = create_app(_config())
        app.state.skill_registry = AsyncMock()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/skills",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403

    async def test_deprecate_skill(self) -> None:
        sid = uuid4()
        registry: Any = AsyncMock()
        registry.get = AsyncMock(return_value=_skill_spec(skill_id=sid))
        registry.deprecate = AsyncMock(return_value=None)

        app = create_app(_config())
        app.state.skill_registry = registry
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/admin/skills/{sid}/deprecate",
                json={"reason": "obsolete"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deprecated"


# ============================================================
# Memory
# ============================================================


def _memory_row(
    *, mem_id: UUID | None = None, content: str = "User prefers dark mode"
) -> dict[str, Any]:
    return {
        "id": mem_id or uuid4(),
        "tenant_id": TID,
        "scope": "personal",
        "owner_id": EMP_ID,
        "memory_type": "preference",
        "content": content,
        "confidence": 0.9,
        "source": "agent",
        "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        "last_accessed": None,
        "access_count": 0,
    }


class TestMemoryList:
    async def test_list_memories(self) -> None:
        rows = [_memory_row(content="prefers dark mode"), _memory_row(content="likes tea")]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=rows)
        app.state.memory_engine = AsyncMock()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/memory",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["content"] == "prefers dark mode"

    async def test_empty_memories(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=[])
        app.state.memory_engine = AsyncMock()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/memory",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_no_token_returns_401(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        app.state.memory_engine = AsyncMock()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/memory")
        assert resp.status_code == 401


class TestMemoryDelete:
    async def test_delete_memory(self) -> None:
        mid = uuid4()
        app = create_app(_config())
        app.state.db = _mock_db(single_row={"id": mid})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                f"/memory/{mid}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 204
        app.state.db.execute.assert_awaited()

    async def test_delete_not_found(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                f"/memory/{uuid4()}",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 404
