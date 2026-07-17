"""Tests for SkillResolverImpl — visibility union + agent assignment."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.skills.resolver import SkillResolverImpl, select_exact_skill


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
        assert "s.tenant_id = :tenant_id" in sql
        assert "JOIN iam.departments" in sql
        assert "d.tenant_id = :tenant_id" in sql
        assert "s.owner_id IS NULL" in sql

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
        assert "s.tenant_id = :tenant_id" in sql
        assert "s.status = 'published'" in sql

    async def test_returns_empty_when_no_assignments(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = []
        resolver = SkillResolverImpl(db)

        results = await resolver.resolve_for_agent(uuid4(), uuid4())
        assert results == []


class TestSelectExactSkill:
    def test_returns_the_only_exact_match(self) -> None:
        first = _row()
        first["name"] = "daily-report"
        second = _row()
        second["name"] = "inventory-check"
        from eaos.skills.registry import _row_to_spec

        skills = [_row_to_spec(first), _row_to_spec(second)]
        assert select_exact_skill(skills, "inventory-check") is skills[1]

    def test_near_match_and_case_change_fail_closed(self) -> None:
        from eaos.skills.registry import _row_to_spec

        skill = _row_to_spec(_row())
        assert select_exact_skill([skill], "skill") is None
        assert select_exact_skill([skill], "Skill-X") is None
        assert select_exact_skill([skill], "skill-x ") is None

    def test_duplicate_exact_names_are_ambiguous(self) -> None:
        from eaos.skills.registry import _row_to_spec

        skills = [_row_to_spec(_row()), _row_to_spec(_row())]
        assert select_exact_skill(skills, "skill-x") is None

    def test_non_string_or_empty_name_fails_closed(self) -> None:
        assert select_exact_skill([], None) is None
        assert select_exact_skill([], "") is None
