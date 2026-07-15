"""Tests for CostGovernorImpl — quota tracking and budget enforcement."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from eaos.core.errors import QuotaExceededError
from eaos.harness.context import GuardContext
from eaos.harness.cost.governor import (
    CostGovernorImpl,
    QuotaScope,
    QuotaStatus,
)


class FakeCostDb:
    """In-memory CostDb with configurable quota rows per (scope, owner_id)."""

    def __init__(self, quotas: dict[tuple[str, str | None], dict[str, Any]] | None = None) -> None:
        self._quotas = quotas or {}
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        scope = str(params[1])
        owner: str | None
        if "owner_id IS NULL" in sql:
            owner = None
        else:
            raw_owner = params[2]
            owner = str(raw_owner) if isinstance(raw_owner, UUID) else raw_owner
        key: tuple[str, str | None] = (scope, owner)
        return self._quotas.get(key)

    async def execute(self, sql: str, *params: Any) -> None:
        self.executed.append((sql, params))


def _quota_row(
    *,
    tenant_id: Any = None,
    scope: str = "agent",
    owner_id: Any = None,
    period: str = "daily",
    token_limit: int = 10000,
    token_used: int = 0,
    cost_limit_usd: float | None = 100.0,
    cost_used_usd: float = 0.0,
    reset_at: str = "2026-12-31T23:59:59Z",
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id or uuid4(),
        "scope": scope,
        "owner_id": str(owner_id) if owner_id else None,
        "period": period,
        "token_limit": token_limit,
        "token_used": token_used,
        "cost_limit_usd": cost_limit_usd,
        "cost_used_usd": cost_used_usd,
        "reset_at": reset_at,
    }


def _ctx(
    *,
    agent_id: Any = None,
    department_ids: list[Any] | None = None,
) -> GuardContext:
    return GuardContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=agent_id or uuid4(),
        agent_scope="personal",
        department_ids=department_ids or [],
    )


class TestCheckQuota:
    async def test_no_quota_configured_passes(self) -> None:
        db = FakeCostDb(quotas={})
        governor = CostGovernorImpl(db)

        await governor.check_quota(_ctx())  # should not raise

    async def test_quota_within_limit_passes(self) -> None:
        agent_id = uuid4()
        db = FakeCostDb(quotas={
            ("agent", str(agent_id)): _quota_row(
                scope="agent", owner_id=agent_id, token_limit=10000, token_used=5000
            ),
        })
        governor = CostGovernorImpl(db)

        await governor.check_quota(_ctx(agent_id=agent_id))

    async def test_token_quota_exceeded_raises(self) -> None:
        agent_id = uuid4()
        db = FakeCostDb(quotas={
            ("agent", str(agent_id)): _quota_row(
                scope="agent", owner_id=agent_id, token_limit=10000, token_used=10000
            ),
        })
        governor = CostGovernorImpl(db)

        with pytest.raises(QuotaExceededError, match="token quota exceeded"):
            await governor.check_quota(_ctx(agent_id=agent_id))

    async def test_cost_quota_exceeded_raises(self) -> None:
        agent_id = uuid4()
        db = FakeCostDb(quotas={
            ("agent", str(agent_id)): _quota_row(
                scope="agent", owner_id=agent_id,
                token_limit=10000, token_used=1000,
                cost_limit_usd=50.0, cost_used_usd=50.0,
            ),
        })
        governor = CostGovernorImpl(db)

        with pytest.raises(QuotaExceededError, match="cost quota exceeded"):
            await governor.check_quota(_ctx(agent_id=agent_id))

    async def test_org_quota_checked(self) -> None:
        tenant = uuid4()
        db = FakeCostDb(quotas={
            ("organization", None): _quota_row(
                tenant_id=tenant, scope="organization", owner_id=None,
                token_limit=100000, token_used=100000, period="monthly",
            ),
        })
        governor = CostGovernorImpl(db)

        with pytest.raises(QuotaExceededError, match="organization"):
            await governor.check_quota(_ctx())


class TestConsume:
    async def test_consume_updates_all_tiers(self) -> None:
        agent_id = uuid4()
        dept_id = uuid4()
        tenant = uuid4()
        db = FakeCostDb(quotas={
            ("organization", None): _quota_row(
                tenant_id=tenant, scope="organization", owner_id=None, period="monthly"
            ),
            ("agent", str(agent_id)): _quota_row(
                tenant_id=tenant, scope="agent", owner_id=agent_id, period="daily"
            ),
            ("department", str(dept_id)): _quota_row(
                tenant_id=tenant, scope="department", owner_id=dept_id, period="monthly"
            ),
        })
        governor = CostGovernorImpl(db)
        ctx = _ctx(agent_id=agent_id, department_ids=[dept_id])

        await governor.consume(ctx, tokens=500, cost_usd=2.5)

        assert len(db.executed) == 3
        for sql, params in db.executed:
            assert "UPDATE harness.quotas" in sql
            assert "token_used = token_used + :p0" in sql
            assert 500 in params
            assert 2.5 in params

    async def test_consume_skips_missing_quotas(self) -> None:
        db = FakeCostDb(quotas={})
        governor = CostGovernorImpl(db)

        await governor.consume(_ctx(), tokens=100)

        assert len(db.executed) == 0

    async def test_consume_zero_cost(self) -> None:
        agent_id = uuid4()
        db = FakeCostDb(quotas={
            ("agent", str(agent_id)): _quota_row(scope="agent", owner_id=agent_id),
        })
        governor = CostGovernorImpl(db)

        await governor.consume(_ctx(agent_id=agent_id), tokens=100, cost_usd=None)

        assert len(db.executed) == 1
        _, params = db.executed[0]
        assert 0.0 in params


class TestReserveAndRelease:
    async def test_reserve_increments_usage(self) -> None:
        agent_id = uuid4()
        db = FakeCostDb(quotas={
            ("agent", str(agent_id)): _quota_row(scope="agent", owner_id=agent_id),
        })
        governor = CostGovernorImpl(db)

        await governor.reserve(_ctx(agent_id=agent_id), tokens=200)

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "token_used + :p0" in sql
        assert 200 in params

    async def test_release_decrements_usage(self) -> None:
        agent_id = uuid4()
        db = FakeCostDb(quotas={
            ("agent", str(agent_id)): _quota_row(scope="agent", owner_id=agent_id),
        })
        governor = CostGovernorImpl(db)

        await governor.release_reservation(_ctx(agent_id=agent_id), tokens=100)

        assert len(db.executed) == 1
        sql, _ = db.executed[0]
        assert "GREATEST(0, token_used - :p0)" in sql


class TestDegrade:
    async def test_degrade_calls_fallback(self) -> None:
        governor = CostGovernorImpl(FakeCostDb())

        def cheap_fn(x: int, y: int = 0) -> int:
            return x + y

        result = await governor.degrade(_ctx(), cheap_fn, (5,), {"y": 3})

        assert result == 8


class TestGetStatus:
    async def test_returns_status_when_quota_exists(self) -> None:
        agent_id = uuid4()
        tenant = uuid4()
        db = FakeCostDb(quotas={
            ("agent", str(agent_id)): _quota_row(
                tenant_id=tenant, scope="agent", owner_id=agent_id,
                token_limit=10000, token_used=3000,
                cost_limit_usd=50.0, cost_used_usd=15.0,
            ),
        })
        governor = CostGovernorImpl(db)

        status = await governor.get_status(tenant, QuotaScope.AGENT, agent_id)

        assert isinstance(status, QuotaStatus)
        assert status.config.token_limit == 10000
        assert status.token_used == 3000
        assert status.remaining_tokens == 7000
        assert status.remaining_cost_usd == 35.0
        assert status.utilization_pct == 30.0

    async def test_returns_empty_status_when_no_quota(self) -> None:
        governor = CostGovernorImpl(FakeCostDb())

        status = await governor.get_status(uuid4(), QuotaScope.ORGANIZATION, None)

        assert status.token_used == 0
        assert status.config.token_limit == 0
        assert status.remaining_cost_usd is None
        assert status.utilization_pct == 0.0

    async def test_status_for_org_scope(self) -> None:
        tenant = uuid4()
        db = FakeCostDb(quotas={
            ("organization", None): _quota_row(
                tenant_id=tenant, scope="organization", owner_id=None,
                token_limit=1000000, token_used=500000, period="monthly",
                cost_limit_usd=None, cost_used_usd=0.0,
            ),
        })
        governor = CostGovernorImpl(db)

        status = await governor.get_status(tenant, QuotaScope.ORGANIZATION, None)

        assert status.config.period == "monthly"
        assert status.config.cost_limit_usd is None
        assert status.remaining_cost_usd is None
        assert status.utilization_pct == 50.0
