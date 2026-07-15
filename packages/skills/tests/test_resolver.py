"""Tests for SkillResolverImpl — visibility union + agent assignment."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.skills.resolver import SkillResolverImpl


def _row() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "scope": "personal",
        "owner_id": uuid4(),
        "name": "skill-x",
        "display_name": "Skill X",
        "description": "desc",
        "category": "data_analysis",
        "risk_level": "low",
        "guardrail": {"instructions": "x", "tools": []},
        "status": "published",
        "version": "0.1.0",
    }


class TestResolveForUser:
    async def test_query_contains_visibility_union(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [_row()]
        resolver = SkillResolverImpl(db)

        tenant_id = uuid4()
        user_id = uuid4()
        await resolver.resolve_for_user(tenant_id, user_id)

        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "scope = 'personal'" in sql
        assert "iam.memberships" in sql
        assert "scope = 'company'" in sql
        assert "status = 'published'" in sql

    async def test_returns_specs(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [_row(), _row()]
        resolver = SkillResolverImpl(db)

        results = await resolver.resolve_for_user(uuid4(), uuid4())
        assert len(results) == 2
        assert all(r.name == "skill-x" for r in results)


class TestResolveForAgent:
    async def test_query_joins_agent_skills(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [_row()]
        resolver = SkillResolverImpl(db)

        agent_id = uuid4()
        tenant_id = uuid4()
        await resolver.resolve_for_agent(agent_id, tenant_id)

        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "JOIN agent.agent_skills" in sql
        assert "a.agent_id = :p0" in sql
        assert "a.enabled = TRUE" in sql

    async def test_returns_empty_when_no_assignments(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = []
        resolver = SkillResolverImpl(db)

        results = await resolver.resolve_for_agent(uuid4(), uuid4())
        assert results == []
