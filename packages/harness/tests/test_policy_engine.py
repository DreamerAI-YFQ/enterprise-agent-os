"""Tests for PolicyEngineImpl — publish, activate, rollback, shadow, list."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from eaos.core.errors import NotFoundError
from eaos.harness.policy import (
    Policy,
    PolicyEngineImpl,
    PolicyStatus,
)


class FakePolicyDb:
    """In-memory PolicyDb with configurable fetch results."""

    def __init__(
        self,
        one_row: dict[str, Any] | None = None,
        many_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._one = one_row
        self._many = many_rows or []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        return self._one

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        return list(self._many)

    async def execute(self, sql: str, *params: Any) -> None:
        self.executed.append((sql, params))


def _policy(
    *,
    name: str = "capability.personal_agent",
    version: str = "1.0.0",
    content: dict[str, Any] | None = None,
    status: PolicyStatus = PolicyStatus.DRAFT,
    tenant_id: Any = None,
) -> Policy:
    return Policy(
        name=name,
        version=version,
        content=content or {"max_models": 3},
        status=status,
        tenant_id=tenant_id,
    )


def _row(
    *,
    name: str = "capability.personal_agent",
    version: str = "1.0.0",
    content: dict[str, Any] | None = None,
    status: str = "draft",
    tenant_id: Any = None,
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "name": name,
        "version": version,
        "content": json.dumps(content or {"max_models": 3}),
        "status": status,
        "tenant_id": tenant_id,
    }


class TestPublish:
    async def test_inserts_new_version_as_draft(self) -> None:
        db = FakePolicyDb()
        engine = PolicyEngineImpl(db)
        policy = _policy()

        await engine.publish(policy)

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "INSERT INTO harness.policies" in sql
        assert params[1] == policy.name
        assert params[2] == policy.version
        assert params[4] == "draft"


class TestLoad:
    async def test_load_active_version(self) -> None:
        db = FakePolicyDb(one_row=_row(status="active", content={"max_models": 5}))
        engine = PolicyEngineImpl(db)

        policy = await engine.load("capability.personal_agent")

        assert policy.name == "capability.personal_agent"
        assert policy.status == PolicyStatus.ACTIVE
        assert policy.content == {"max_models": 5}

    async def test_load_specific_version(self) -> None:
        db = FakePolicyDb(
            one_row=_row(version="2.1.0", status="rollback", content={"limit": 10})
        )
        engine = PolicyEngineImpl(db)

        policy = await engine.load("capability.personal_agent", version="2.1.0")

        assert policy.version == "2.1.0"
        assert policy.status == PolicyStatus.ROLLBACK

    async def test_raises_not_found_when_missing(self) -> None:
        db = FakePolicyDb(one_row=None)
        engine = PolicyEngineImpl(db)

        with pytest.raises(NotFoundError, match="policy"):
            await engine.load("nonexistent.policy")


class TestActivate:
    async def test_rolls_back_previous_and_activates_new(self) -> None:
        db = FakePolicyDb()
        engine = PolicyEngineImpl(db)

        await engine.activate("capability.personal_agent", "2.0.0")

        assert len(db.executed) == 2
        # First: rollback previous active
        sql1, params1 = db.executed[0]
        assert "status = 'rollback'" in sql1
        assert "status = 'active'" in sql1
        # Second: activate new version
        sql2, params2 = db.executed[1]
        assert "status = 'active'" in sql2
        assert params2[1] == "2.0.0"


class TestRollback:
    async def test_rolls_back_current_and_activates_target(self) -> None:
        db = FakePolicyDb()
        engine = PolicyEngineImpl(db)

        await engine.rollback("capability.personal_agent", "1.0.0")

        assert len(db.executed) == 2
        sql2, params2 = db.executed[1]
        assert "status = 'active'" in sql2
        assert params2[1] == "1.0.0"


class TestShadowMode:
    async def test_updates_status_to_shadow(self) -> None:
        db = FakePolicyDb()
        engine = PolicyEngineImpl(db)
        policy = _policy(version="1.5.0")

        await engine.shadow_mode(policy)

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "status = 'shadow'" in sql
        assert params[0] == policy.name
        assert params[1] == policy.version


class TestListVersions:
    async def test_returns_all_versions(self) -> None:
        db = FakePolicyDb(
            many_rows=[
                _row(version="3.0.0", status="draft"),
                _row(version="2.0.0", status="active"),
                _row(version="1.0.0", status="rollback"),
            ]
        )
        engine = PolicyEngineImpl(db)

        versions = await engine.list_versions("capability.personal_agent")

        assert len(versions) == 3
        assert versions[0].version == "3.0.0"
        assert versions[1].version == "2.0.0"
        assert versions[2].version == "1.0.0"

    async def test_returns_empty_when_none(self) -> None:
        db = FakePolicyDb(many_rows=[])
        engine = PolicyEngineImpl(db)

        versions = await engine.list_versions("nonexistent")

        assert versions == []

    async def test_parses_dict_content(self) -> None:
        db = FakePolicyDb(
            many_rows=[_row(content={"key": "value"}, status="active")]
        )
        engine = PolicyEngineImpl(db)

        versions = await engine.list_versions("test")

        assert versions[0].content == {"key": "value"}
