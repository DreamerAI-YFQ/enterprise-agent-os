"""Tests for PgSkillRegistry — CRUD, lifecycle, guardrail packing, tool_bindings."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from eaos.core.errors import NotFoundError
from eaos.skills.registry import (
    PgSkillRegistry,
    _pack_guardrail,
    _parse_tool_bindings,
    _row_to_spec,
    _tool_bindings_to_json,
)
from eaos.skills.spec import (
    GuardrailConfig,
    RiskLevel,
    SkillCategory,
    SkillScope,
    SkillSpec,
    ToolBinding,
)


def _spec(
    *,
    category: SkillCategory = SkillCategory.DATA_ANALYSIS,
    guardrail: GuardrailConfig | None = None,
    tool_bindings: list[ToolBinding] | None = None,
    skill_id: UUID | None = None,
) -> SkillSpec:
    return SkillSpec(
        id=skill_id or uuid4(),
        tenant_id=uuid4(),
        scope=SkillScope.PERSONAL,
        owner_id=uuid4(),
        name="skill-x",
        display_name="Skill X",
        description="desc",
        category=category,
        risk_level=RiskLevel.LOW,
        instructions="do thing",
        tools=["code_execution"],
        tool_bindings=tool_bindings or [],
        guardrail=guardrail,
        version="0.1.0",
        status="draft",
    )


def _row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "scope": "personal",
        "owner_id": None,
        "name": "skill-x",
        "display_name": "Skill X",
        "description": "desc",
        "category": "data_analysis",
        "risk_level": "low",
        "guardrail": {},
        "instructions": "do thing",
        "tools": ["code_execution"],
        "tool_bindings": [],
        "status": "draft",
        "version": "0.1.0",
    }
    base.update(overrides)
    return base


class TestCreate:
    async def test_create_returns_spec(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = [_row()]
        registry = PgSkillRegistry(db)

        await registry.create(_spec(), uuid4())

        db.fetch.assert_awaited_once()
        assert "INSERT INTO skills.skills" in db.fetch.call_args.args[0]

    async def test_create_production_without_guardrail_raises(self) -> None:
        db = AsyncMock()
        registry = PgSkillRegistry(db)

        with pytest.raises(ValueError, match="requires a guardrail"):
            await registry.create(
                _spec(category=SkillCategory.SYSTEM_OPERATION), uuid4()
            )

    async def test_create_production_with_guardrail_ok(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = [_row(category="system_operation")]
        registry = PgSkillRegistry(db)

        spec = _spec(
            category=SkillCategory.SYSTEM_OPERATION,
            guardrail=GuardrailConfig(confirm_required=True),
        )
        await registry.create(spec, uuid4())
        db.fetch.assert_awaited_once()

    async def test_pack_guardrail_excludes_instructions_and_tools(self) -> None:
        """T4: guardrail blob only stores guardrail config, not instructions/tools."""
        spec = _spec(
            guardrail=GuardrailConfig(confirm_required=True, notify_channels=["slack"]),
        )
        blob = json.loads(_pack_guardrail(spec))
        # guardrail config preserved
        assert blob["confirm_required"] is True
        assert blob["notify_channels"] == ["slack"]
        # instructions/tools NOT in blob (live in own columns now)
        assert "instructions" not in blob
        assert "tools" not in blob

    async def test_pack_guardrail_empty_when_none(self) -> None:
        spec = _spec(guardrail=None)
        assert _pack_guardrail(spec) == "{}"


class TestGet:
    async def test_get_returns_spec(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [_row()]
        registry = PgSkillRegistry(db)

        result = await registry.get(uuid4(), uuid4())
        assert result.name == "skill-x"

    async def test_get_missing_raises(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = []
        registry = PgSkillRegistry(db)

        with pytest.raises(NotFoundError):
            await registry.get(uuid4(), uuid4())


class TestRowToSpec:
    def test_prefers_dedicated_columns_over_blob(self) -> None:
        """T4: new columns take precedence over legacy guardrail blob."""
        row = _row(
            instructions="from column",
            tools=["a", "b"],
            guardrail={"instructions": "from blob", "tools": ["x"], "confirm_required": True},
        )
        spec = _row_to_spec(row)
        assert spec.instructions == "from column"
        assert spec.tools == ["a", "b"]
        assert spec.guardrail is not None
        assert spec.guardrail.confirm_required is True

    def test_falls_back_to_blob_for_unmigrated_rows(self) -> None:
        """Compat: rows where new columns are NULL fall back to guardrail blob."""
        row = _row(
            instructions=None,
            tools=None,
            guardrail={"instructions": "legacy", "tools": ["t1"]},
        )
        spec = _row_to_spec(row)
        assert spec.instructions == "legacy"
        assert spec.tools == ["t1"]

    def test_no_guardrail_returns_none(self) -> None:
        spec = _row_to_spec(_row(guardrail={}, instructions="x"))
        assert spec.guardrail is None
        assert spec.instructions == "x"

    def test_guardrail_string_parsed(self) -> None:
        row = _row(
            instructions=None,
            tools=None,
            guardrail=json.dumps({"instructions": "y", "tools": ["t"]}),
        )
        spec = _row_to_spec(row)
        assert spec.instructions == "y"
        assert spec.tools == ["t"]

    def test_parses_tool_bindings_from_column(self) -> None:
        row = _row(
            tool_bindings=[
                {
                    "tool_name": "erp.create_order",
                    "param_mapping": {"customer": "customer_id"},
                    "required": True,
                    "description": "Create order",
                }
            ]
        )
        spec = _row_to_spec(row)
        assert len(spec.tool_bindings) == 1
        b = spec.tool_bindings[0]
        assert b.tool_name == "erp.create_order"
        assert b.param_mapping == {"customer": "customer_id"}
        assert b.required is True
        assert b.description == "Create order"

    def test_parses_tool_bindings_string(self) -> None:
        raw = json.dumps(
            [{"tool_name": "t1", "param_mapping": {}, "required": False}]
        )
        spec = _row_to_spec(_row(tool_bindings=raw))
        assert len(spec.tool_bindings) == 1
        assert spec.tool_bindings[0].tool_name == "t1"
        assert spec.tool_bindings[0].required is False


class TestToolBindingsSerialization:
    def test_to_json_round_trip(self) -> None:
        bindings = [
            ToolBinding(
                tool_name="erp.create_order",
                param_mapping={"customer": "customer_id", "amount": "total"},
                required=True,
                description="Create order",
            ),
            ToolBinding(
                tool_name="crm.update_lead",
                param_mapping={},
                required=False,
                description=None,
            ),
        ]
        s = _tool_bindings_to_json(bindings)
        parsed = _parse_tool_bindings(s)
        assert len(parsed) == 2
        assert parsed[0].tool_name == "erp.create_order"
        assert parsed[0].param_mapping == {"customer": "customer_id", "amount": "total"}
        assert parsed[0].required is True
        assert parsed[1].tool_name == "crm.update_lead"
        assert parsed[1].required is False
        assert parsed[1].description is None

    def test_parse_empty(self) -> None:
        assert _parse_tool_bindings(None) == []
        assert _parse_tool_bindings("") == []
        assert _parse_tool_bindings("[]") == []
        assert _parse_tool_bindings([]) == []


class TestUpdate:
    async def test_update_builds_dynamic_sql(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [_row(name="new-name")]
        registry = PgSkillRegistry(db)

        result = await registry.update(uuid4(), uuid4(), {"name": "new-name"})

        assert result.name == "new-name"
        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "UPDATE skills.skills SET" in sql
        assert "name = :p0" in sql

    async def test_update_empty_returns_get(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [_row()]
        registry = PgSkillRegistry(db)

        await registry.update(uuid4(), uuid4(), {})
        assert db.tenant_scoped_fetch.await_count == 1

    async def test_update_instructions(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [_row(instructions="new instr")]
        registry = PgSkillRegistry(db)

        result = await registry.update(
            uuid4(), uuid4(), {"instructions": "new instr"}
        )
        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "instructions = :p0" in sql
        assert result.instructions == "new instr"

    async def test_update_tool_bindings(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [_row()]
        registry = PgSkillRegistry(db)

        bindings = [ToolBinding(tool_name="erp.create_order", param_mapping={})]
        await registry.update(uuid4(), uuid4(), {"tool_bindings": bindings})

        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "tool_bindings = CAST(:p0 AS jsonb)" in sql


class TestPublishAndDeprecate:
    async def test_publish_sets_status(self) -> None:
        db = AsyncMock()
        registry = PgSkillRegistry(db)

        await registry.publish(uuid4(), uuid4(), uuid4())

        sql = db.execute.call_args.args[0]
        assert "status = 'published'" in sql

    async def test_deprecate_sets_status(self) -> None:
        db = AsyncMock()
        registry = PgSkillRegistry(db)

        await registry.deprecate(uuid4(), uuid4(), "broken")

        sql = db.execute.call_args.args[0]
        assert "status = 'deprecated'" in sql


class TestListByTenant:
    async def test_list_with_filters(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = [_row(), _row()]
        registry = PgSkillRegistry(db)

        results = await registry.list_by_tenant(
            uuid4(), filters={"status": "published"}
        )
        assert len(results) == 2
        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "status = :p0" in sql

    async def test_list_no_filters(self) -> None:
        db = AsyncMock()
        db.tenant_scoped_fetch.return_value = []
        registry = PgSkillRegistry(db)

        await registry.list_by_tenant(uuid4())
        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "WHERE" not in sql
