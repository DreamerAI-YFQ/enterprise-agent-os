"""Skill registry protocol — CRUD + lifecycle."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

from eaos.core.errors import NotFoundError
from eaos.skills.spec import (
    GuardrailConfig,
    RiskLevel,
    SkillCategory,
    SkillScope,
    SkillSpec,
    ToolBinding,
)

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient


class SkillRegistry(Protocol):
    """Skill CRUD and lifecycle management."""

    async def create(
        self,
        spec: SkillSpec,
        creator: UUID,
    ) -> SkillSpec:
        """Create a new skill. Validates guardrail for production categories."""
        ...

    async def get(self, skill_id: UUID, tenant_id: UUID) -> SkillSpec:
        """Fetch by id. Raises NotFoundError if missing."""
        ...

    async def update(
        self,
        skill_id: UUID,
        tenant_id: UUID,
        updates: dict[str, Any],
    ) -> SkillSpec:
        """Partial update."""
        ...

    async def publish(
        self,
        skill_id: UUID,
        tenant_id: UUID,
        publisher: UUID,
    ) -> None:
        """Publish a skill (department/company scope requires review)."""
        ...

    async def deprecate(
        self,
        skill_id: UUID,
        tenant_id: UUID,
        reason: str,
    ) -> None:
        """Mark skill as deprecated, notify assignees."""
        ...

    async def delete(self, skill_id: UUID, tenant_id: UUID) -> None:
        """Hard-delete a skill (drafts only; enforced at route level)."""
        ...

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        filters: dict[str, Any] | None = None,
    ) -> list[SkillSpec]:
        """List skills for a tenant with optional filters."""
        ...


def _pack_guardrail(spec: SkillSpec) -> str:
    """Serialize guardrail config only (instructions/tools live in own columns)."""
    if spec.guardrail is None:
        return "{}"
    data: dict[str, Any] = {
        "confirm_required": spec.guardrail.confirm_required,
        "auto_confirm_conditions": list(spec.guardrail.auto_confirm_conditions),
        "notify_channels": list(spec.guardrail.notify_channels),
        "rollback_enabled": spec.guardrail.rollback_enabled,
    }
    return json.dumps(data)


def _tool_bindings_to_json(bindings: list[ToolBinding]) -> str:
    """Serialize tool bindings to JSONB-friendly string."""
    data = [
        {
            "tool_name": b.tool_name,
            "param_mapping": dict(b.param_mapping),
            "required": b.required,
            "description": b.description,
        }
        for b in bindings
    ]
    return json.dumps(data)


def _parse_tool_bindings(raw: Any) -> list[ToolBinding]:
    """Parse tool bindings from a DB column value (str | list | None)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        if not raw:
            return []
        raw = json.loads(raw)
    if not isinstance(raw, list):
        return []
    bindings: list[ToolBinding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        mapping_raw = item.get("param_mapping", {})
        if isinstance(mapping_raw, dict):
            mapping = {str(k): str(v) for k, v in mapping_raw.items()}
        else:
            mapping = {}
        bindings.append(
            ToolBinding(
                tool_name=str(item.get("tool_name", "")),
                param_mapping=mapping,
                required=bool(item.get("required", True)),
                description=item.get("description"),
            )
        )
    return bindings


def _row_to_spec(row: dict[str, Any]) -> SkillSpec:
    """Map a DB row to SkillSpec.

    Prefers the dedicated ``instructions`` / ``tools`` / ``tool_bindings``
    columns (migration 0009); falls back to the legacy ``guardrail`` blob for
    rows not yet migrated.
    """
    guardrail_raw = row.get("guardrail") or {}
    if isinstance(guardrail_raw, str):
        guardrail_raw = json.loads(guardrail_raw)

    # Instructions: prefer column, fall back to blob
    instructions = row.get("instructions")
    if instructions is None:
        instructions = guardrail_raw.get("instructions", "")
    instructions = str(instructions) if instructions is not None else ""

    # Tools: prefer column, fall back to blob
    tools_raw = row.get("tools")
    if tools_raw is None:
        tools_raw = guardrail_raw.get("tools", [])
    if isinstance(tools_raw, str):
        tools_raw = json.loads(tools_raw) if tools_raw else []
    tools = [str(t) for t in tools_raw] if isinstance(tools_raw, list) else []

    # Tool bindings: prefer column (migration 0009+)
    tool_bindings = _parse_tool_bindings(row.get("tool_bindings"))

    # Guardrail config (confirm_required, etc.) lives in the blob
    guardrail: GuardrailConfig | None = None
    if "confirm_required" in guardrail_raw:
        guardrail = GuardrailConfig(
            confirm_required=bool(guardrail_raw["confirm_required"]),
            auto_confirm_conditions=list(guardrail_raw.get("auto_confirm_conditions", [])),
            notify_channels=list(guardrail_raw.get("notify_channels", [])),
            rollback_enabled=bool(guardrail_raw.get("rollback_enabled", False)),
        )

    return SkillSpec(
        id=row["id"],
        tenant_id=row["tenant_id"],
        scope=SkillScope(row["scope"]),
        owner_id=row.get("owner_id"),
        name=row["name"],
        display_name=row["display_name"],
        description=row["description"],
        category=SkillCategory(row["category"]),
        risk_level=RiskLevel(row["risk_level"]),
        instructions=instructions,
        tools=tools,
        tool_bindings=tool_bindings,
        guardrail=guardrail,
        version=str(row.get("version", "0.1.0")),
        status=str(row.get("status", "draft")),
    )


_SELECT_COLS = (
    "id, tenant_id, scope, owner_id, name, display_name, description, "
    "category, risk_level, guardrail, instructions, tools, tool_bindings, "
    "status, version"
)


class PgSkillRegistry:
    """SkillRegistry backed by PostgreSQL."""

    def __init__(self, db: DbClient) -> None:
        self._db = db

    async def create(self, spec: SkillSpec, creator: UUID) -> SkillSpec:
        del creator  # Phase 3: no review workflow; creator recorded via trace only.
        if spec.requires_guardrail and spec.guardrail is None:
            raise ValueError(
                f"production-tier skill category '{spec.category}' requires a guardrail"
            )
        rows = await self._db.fetch(
            "INSERT INTO skills.skills "
            "(id, tenant_id, scope, owner_id, name, display_name, description, "
            "category, risk_level, guardrail, instructions, tools, tool_bindings, "
            "status, version) "
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, "
            "CAST(:p9 AS jsonb), :p10, CAST(:p11 AS jsonb), CAST(:p12 AS jsonb), "
            ":p13, :p14) "
            f"RETURNING {_SELECT_COLS}",
            spec.id,
            spec.tenant_id,
            str(spec.scope.value),
            spec.owner_id,
            spec.name,
            spec.display_name,
            spec.description,
            str(spec.category.value),
            str(spec.risk_level.value),
            _pack_guardrail(spec),
            spec.instructions,
            json.dumps(list(spec.tools)),
            _tool_bindings_to_json(spec.tool_bindings),
            spec.status,
            spec.version,
        )
        return _row_to_spec(rows[0])

    async def get(self, skill_id: UUID, tenant_id: UUID) -> SkillSpec:
        rows = await self._db.tenant_scoped_fetch(
            f"SELECT {_SELECT_COLS} FROM skills.skills "
            "WHERE id = :p0 AND tenant_id = :tenant_id",
            tenant_id,
            skill_id,
        )
        if not rows:
            raise NotFoundError(f"skill {skill_id} not found")
        return _row_to_spec(rows[0])

    async def update(
        self,
        skill_id: UUID,
        tenant_id: UUID,
        updates: dict[str, Any],
    ) -> SkillSpec:
        if not updates:
            return await self.get(skill_id, tenant_id)
        allowed_scalar = {
            "name", "display_name", "description", "category", "risk_level",
            "status", "version", "instructions", "scope", "owner_id",
        }
        allowed_jsonb = {"tools", "tool_bindings"}
        set_clauses: list[str] = []
        params: list[Any] = []
        for key, value in updates.items():
            if key in allowed_scalar:
                idx = len(params)
                set_clauses.append(f"{key} = :p{idx}")
                params.append(
                    str(value.value) if hasattr(value, "value") else value
                )
            elif key in allowed_jsonb:
                idx = len(params)
                set_clauses.append(f"{key} = CAST(:p{idx} AS jsonb)")
                if key == "tools":
                    params.append(json.dumps(list(value)))
                else:  # tool_bindings
                    params.append(
                        _tool_bindings_to_json(value)
                        if all(isinstance(b, ToolBinding) for b in value)
                        else json.dumps(list(value))
                    )
        if not set_clauses:
            return await self.get(skill_id, tenant_id)
        idx = len(params)
        set_sql = ", ".join(set_clauses)
        rows = await self._db.tenant_scoped_fetch(
            f"UPDATE skills.skills SET {set_sql} "
            f"WHERE id = :p{idx} AND tenant_id = :tenant_id "
            f"RETURNING {_SELECT_COLS}",
            tenant_id,
            *params,
            skill_id,
        )
        if not rows:
            raise NotFoundError(f"skill {skill_id} not found")
        return _row_to_spec(rows[0])

    async def publish(self, skill_id: UUID, tenant_id: UUID, publisher: UUID) -> None:
        del publisher  # Phase 3: DEPARTMENT/COMPANY scope skips review (Phase 4 Harness).
        await self._db.execute(
            "UPDATE skills.skills SET status = 'published' "
            "WHERE id = :p0 AND tenant_id = :p1",
            skill_id,
            tenant_id,
        )

    async def deprecate(self, skill_id: UUID, tenant_id: UUID, reason: str) -> None:
        del reason  # Phase 3: reason recorded via trace; no separate column.
        await self._db.execute(
            "UPDATE skills.skills SET status = 'deprecated' "
            "WHERE id = :p0 AND tenant_id = :p1",
            skill_id,
            tenant_id,
        )

    async def delete(self, skill_id: UUID, tenant_id: UUID) -> None:
        await self._db.execute(
            "DELETE FROM skills.skills WHERE id = :p0 AND tenant_id = :p1",
            skill_id,
            tenant_id,
        )

    async def list_by_tenant(
        self,
        tenant_id: UUID,
        filters: dict[str, Any] | None = None,
    ) -> list[SkillSpec]:
        clauses: list[str] = []
        params: list[Any] = []
        if filters:
            for key, value in filters.items():
                if key not in {"scope", "category", "risk_level", "status", "owner_id"}:
                    continue
                idx = len(params)
                clauses.append(f"{key} = :p{idx}")
                params.append(str(value.value) if hasattr(value, "value") else value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._db.tenant_scoped_fetch(
            f"SELECT {_SELECT_COLS} FROM skills.skills {where} ORDER BY name",
            tenant_id,
            *params,
        )
        return [_row_to_spec(r) for r in rows]
