"""Skill resolver protocol — resolve visible skills for user/agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from eaos.skills.registry import _row_to_spec

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient
    from eaos.skills.spec import SkillSpec


class SkillResolver(Protocol):
    """Resolve available skills for a user or agent based on visibility scope."""

    async def resolve_for_user(
        self,
        tenant_id: UUID,
        user_id: UUID,
        agent_id: UUID | None = None,
    ) -> list[SkillSpec]:
        """List skills visible to user: personal + their departments + company."""
        ...

    async def resolve_for_agent(
        self,
        agent_id: UUID,
        tenant_id: UUID,
    ) -> list[SkillSpec]:
        """List skills assigned to an agent plus user-visible skills."""
        ...


def select_exact_skill(
    skills: list[SkillSpec],
    requested_name: object,
) -> SkillSpec | None:
    """Return one exact visible match, otherwise fail closed.

    Fuzzy, prefix, case-folded, and first-item fallback matching are
    intentionally excluded. Duplicate visible names are ambiguous and also
    return ``None``.
    """
    if not isinstance(requested_name, str) or not requested_name:
        return None
    matches = [skill for skill in skills if skill.name == requested_name]
    return matches[0] if len(matches) == 1 else None


class SkillResolverImpl:
    """SkillResolver backed by PostgreSQL.

    Visibility union for a user:
      - personal skills owned by the user
      - department skills owned by departments the user belongs to
      - company-wide skills (owner_id IS NULL, scope='company')
    Only ``status='published'`` skills are returned.
    """

    def __init__(self, db: DbClient) -> None:
        self._db = db

    async def resolve_for_user(
        self,
        tenant_id: UUID,
        user_id: UUID,
        agent_id: UUID | None = None,
    ) -> list[SkillSpec]:
        del agent_id  # user-visible skills are independent of agent binding.
        # C06/GAP-06: Include instructions, tools, tool_bindings columns
        # so SkillSpec has complete execution metadata.
        rows = await self._db.tenant_scoped_fetch(
            "SELECT s.id, s.tenant_id, s.scope, s.owner_id, s.name, "
            "s.display_name, s.description, s.category, s.risk_level, "
            "s.guardrail, s.status, s.version, "
            "s.instructions, s.tools, s.tool_bindings "
            "FROM skills.skills s "
            "WHERE s.tenant_id = :tenant_id "
            "AND s.status = 'published' AND ("
            "  (s.scope = 'personal' AND s.owner_id = :p0) OR "
            "  (s.scope = 'department' AND s.owner_id IN ("
            "    SELECT m.department_id FROM iam.memberships m "
            "    JOIN iam.departments d ON d.id = m.department_id "
            "    WHERE m.user_id = :p0 AND d.tenant_id = :tenant_id"
            "  )) OR "
            "  (s.scope = 'company' AND s.owner_id IS NULL)"
            ") ORDER BY s.name",
            tenant_id,
            user_id,
        )
        return [_row_to_spec(r) for r in rows]

    async def resolve_for_agent(
        self,
        agent_id: UUID,
        tenant_id: UUID,
    ) -> list[SkillSpec]:
        # C06/GAP-06: Include instructions, tools, tool_bindings columns
        rows = await self._db.tenant_scoped_fetch(
            "SELECT s.id, s.tenant_id, s.scope, s.owner_id, s.name, "
            "s.display_name, s.description, s.category, s.risk_level, "
            "s.guardrail, s.status, s.version, "
            "s.instructions, s.tools, s.tool_bindings "
            "FROM skills.skills s "
            "JOIN agent.agent_skills a ON a.skill_id = s.id "
            "WHERE a.agent_id = :p0 AND a.enabled = TRUE "
            "AND s.tenant_id = :tenant_id "
            "AND s.status = 'published' "
            "ORDER BY s.name",
            tenant_id,
            agent_id,
        )
        return [_row_to_spec(r) for r in rows]
