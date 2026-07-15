"""Tests for CapabilityCheckerImpl — agent boundary enforcement."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from eaos.agent.dispatcher import CapabilityBoundary
from eaos.core.errors import HarnessViolationError, NotFoundError
from eaos.harness.capability.checker import (
    CapabilityCheckerImpl,
    _boundary_to_dict,
    _dict_to_boundary,
)
from eaos.harness.context import GuardContext
from eaos.skills.spec import SkillCategory


class FakeCapabilityDb:
    """In-memory CapabilityDb with configurable capability JSON."""

    def __init__(
        self,
        capability: dict[str, Any] | str | None = None,
        agent_exists: bool = True,
    ) -> None:
        self._capability = capability
        self._agent_exists = agent_exists
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        if not self._agent_exists:
            return None
        return {"capability": self._capability}

    async def execute(self, sql: str, *params: Any) -> None:
        self.executed.append((sql, params))


def _boundary(
    *,
    models: list[str] | None = None,
    read_ds: list[Any] | None = None,
    write_ds: list[Any] | None = None,
    categories: list[SkillCategory] | None = None,
    max_iter: int = 10,
    max_dur: int = 600,
) -> CapabilityBoundary:
    return CapabilityBoundary(
        allowed_models=models or [],
        allowed_datasources=read_ds or [],
        writable_datasources=write_ds or [],
        allowed_skill_categories=categories or [],
        max_iterations=max_iter,
        max_task_duration_sec=max_dur,
    )


def _ctx(
    *,
    attributes: dict[str, Any] | None = None,
) -> GuardContext:
    return GuardContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
        action="execute",
        resource="skill",
        attributes=attributes or {},
    )


class TestCheck:
    async def test_model_allowed(self) -> None:
        db = FakeCapabilityDb(
            capability=_boundary_to_dict(
                _boundary(models=["gpt-4", "claude-3"])
            )
        )
        checker = CapabilityCheckerImpl(db)
        ctx = _ctx(attributes={"model": "gpt-4"})

        await checker.check(ctx)  # should not raise

    async def test_model_denied(self) -> None:
        db = FakeCapabilityDb(
            capability=_boundary_to_dict(_boundary(models=["gpt-4"]))
        )
        checker = CapabilityCheckerImpl(db)
        ctx = _ctx(attributes={"model": "llama-3"})

        with pytest.raises(HarnessViolationError, match="not in allowed_models"):
            await checker.check(ctx)

    async def test_model_not_checked_when_no_list(self) -> None:
        db = FakeCapabilityDb(capability=_boundary_to_dict(_boundary()))
        checker = CapabilityCheckerImpl(db)

        await checker.check(_ctx(attributes={"model": "anything"}))

    async def test_datasource_read_allowed(self) -> None:
        ds = uuid4()
        db = FakeCapabilityDb(
            capability=_boundary_to_dict(_boundary(read_ds=[ds]))
        )
        checker = CapabilityCheckerImpl(db)
        ctx = _ctx(attributes={"datasource": ds, "datasource_mode": "read"})

        await checker.check(ctx)

    async def test_datasource_read_denied(self) -> None:
        db = FakeCapabilityDb(
            capability=_boundary_to_dict(_boundary(read_ds=[uuid4()]))
        )
        checker = CapabilityCheckerImpl(db)
        ctx = _ctx(attributes={"datasource": uuid4(), "datasource_mode": "read"})

        with pytest.raises(HarnessViolationError, match="not in allowed_datasources"):
            await checker.check(ctx)

    async def test_datasource_write_allowed(self) -> None:
        ds = uuid4()
        db = FakeCapabilityDb(
            capability=_boundary_to_dict(_boundary(write_ds=[ds]))
        )
        checker = CapabilityCheckerImpl(db)
        ctx = _ctx(attributes={"datasource": ds, "datasource_mode": "write"})

        await checker.check(ctx)

    async def test_datasource_write_denied(self) -> None:
        ds = uuid4()
        db = FakeCapabilityDb(
            capability=_boundary_to_dict(_boundary(write_ds=[uuid4()]))
        )
        checker = CapabilityCheckerImpl(db)
        ctx = _ctx(attributes={"datasource": ds, "datasource_mode": "write"})

        with pytest.raises(HarnessViolationError, match="not writable"):
            await checker.check(ctx)

    async def test_skill_category_allowed(self) -> None:
        db = FakeCapabilityDb(
            capability=_boundary_to_dict(
                _boundary(categories=[SkillCategory.DATA_ANALYSIS])
            )
        )
        checker = CapabilityCheckerImpl(db)
        ctx = _ctx(attributes={"skill_category": SkillCategory.DATA_ANALYSIS})

        await checker.check(ctx)

    async def test_skill_category_denied(self) -> None:
        db = FakeCapabilityDb(
            capability=_boundary_to_dict(
                _boundary(categories=[SkillCategory.DATA_ANALYSIS])
            )
        )
        checker = CapabilityCheckerImpl(db)
        ctx = _ctx(attributes={"skill_category": SkillCategory.VERIFICATION})

        with pytest.raises(HarnessViolationError, match="skill category"):
            await checker.check(ctx)

    async def test_skill_category_string_allowed(self) -> None:
        db = FakeCapabilityDb(
            capability=_boundary_to_dict(
                _boundary(categories=[SkillCategory.DATA_ANALYSIS])
            )
        )
        checker = CapabilityCheckerImpl(db)
        ctx = _ctx(attributes={"skill_category": "data_analysis"})

        await checker.check(ctx)

    async def test_no_attributes_passes(self) -> None:
        db = FakeCapabilityDb(capability=_boundary_to_dict(_boundary()))
        checker = CapabilityCheckerImpl(db)

        await checker.check(_ctx())


class TestGetBoundary:
    async def test_returns_boundary_from_db(self) -> None:
        ds = uuid4()
        db = FakeCapabilityDb(
            capability={
                "allowed_models": ["gpt-4"],
                "allowed_datasources": [str(ds)],
                "writable_datasources": [],
                "allowed_skill_categories": ["data_analysis"],
                "max_task_duration_sec": 300,
                "max_iterations": 5,
            }
        )
        checker = CapabilityCheckerImpl(db)

        boundary = await checker.get_boundary(uuid4(), uuid4())

        assert boundary.allowed_models == ["gpt-4"]
        assert ds in boundary.allowed_datasources
        assert SkillCategory.DATA_ANALYSIS in boundary.allowed_skill_categories
        assert boundary.max_task_duration_sec == 300
        assert boundary.max_iterations == 5

    async def test_agent_not_found_raises(self) -> None:
        db = FakeCapabilityDb(agent_exists=False)
        checker = CapabilityCheckerImpl(db)

        with pytest.raises(NotFoundError, match="not found"):
            await checker.get_boundary(uuid4(), uuid4())

    async def test_null_capability_returns_default(self) -> None:
        db = FakeCapabilityDb(capability=None)
        checker = CapabilityCheckerImpl(db)

        boundary = await checker.get_boundary(uuid4(), uuid4())

        assert boundary.allowed_models == []
        assert boundary.max_iterations == 10
        assert boundary.max_task_duration_sec == 600

    async def test_string_capability_parsed(self) -> None:
        db = FakeCapabilityDb(
            capability=json.dumps({
                "allowed_models": ["claude-3"],
                "max_iterations": 20,
            })
        )
        checker = CapabilityCheckerImpl(db)

        boundary = await checker.get_boundary(uuid4(), uuid4())

        assert boundary.allowed_models == ["claude-3"]
        assert boundary.max_iterations == 20


class TestUpdateBoundary:
    async def test_updates_and_returns_boundary(self) -> None:
        db = FakeCapabilityDb(capability={})
        checker = CapabilityCheckerImpl(db)
        boundary = _boundary(models=["gpt-4"], max_iter=15)

        result = await checker.update_boundary(uuid4(), uuid4(), boundary)

        assert result is boundary
        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "UPDATE agent.agents SET capability" in sql
        assert "updated_at" in sql
        capability_json = params[0]
        parsed = json.loads(capability_json)
        assert parsed["allowed_models"] == ["gpt-4"]
        assert parsed["max_iterations"] == 15


class TestSerialization:
    def test_round_trip(self) -> None:
        ds1 = uuid4()
        ds2 = uuid4()
        original = CapabilityBoundary(
            allowed_models=["gpt-4", "claude-3"],
            allowed_datasources=[ds1],
            writable_datasources=[ds2],
            allowed_skill_categories=[SkillCategory.DATA_ANALYSIS, SkillCategory.VERIFICATION],
            max_task_duration_sec=120,
            max_iterations=7,
        )

        serialized = _boundary_to_dict(original)
        deserialized = _dict_to_boundary(serialized)

        assert deserialized.allowed_models == original.allowed_models
        assert deserialized.allowed_datasources == original.allowed_datasources
        assert deserialized.writable_datasources == original.writable_datasources
        assert deserialized.allowed_skill_categories == original.allowed_skill_categories
        assert deserialized.max_task_duration_sec == original.max_task_duration_sec
        assert deserialized.max_iterations == original.max_iterations

    def test_dict_to_boundary_defaults(self) -> None:
        boundary = _dict_to_boundary({})

        assert boundary.allowed_models == []
        assert boundary.allowed_datasources == []
        assert boundary.max_task_duration_sec == 600
        assert boundary.max_iterations == 10
