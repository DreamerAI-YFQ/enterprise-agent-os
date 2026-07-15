"""Pillar 3: Cost governance — three-tier quota + smart degradation.

Quotas: organization (monthly), department (monthly), agent (daily). On
exceedance: degrade (cheaper model -> cache -> queue -> reject), not hard
reject. Token consumption is atomic (Redis INCR) to handle concurrent agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from eaos.core.errors import QuotaExceededError

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from eaos.harness.context import GuardContext


class QuotaScope(StrEnum):
    """Three quota tiers."""

    ORGANIZATION = "organization"
    DEPARTMENT = "department"
    AGENT = "agent"


@dataclass(frozen=True)
class QuotaConfig:
    """Quota configuration for a scope."""

    tenant_id: UUID
    scope: QuotaScope
    owner_id: UUID | None  # org: None; dept: dept_id; agent: agent_id
    period: str  # daily/monthly
    token_limit: int
    cost_limit_usd: float | None = None


@dataclass(frozen=True)
class QuotaStatus:
    """Current quota usage status."""

    config: QuotaConfig
    token_used: int
    cost_used_usd: float
    remaining_tokens: int
    remaining_cost_usd: float | None
    utilization_pct: float


class CostDb(Protocol):
    """Minimal DB subset for cost governance."""

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...

    async def execute(self, sql: str, *params: Any) -> None: ...


class CostGovernor(Protocol):
    """Pillar 3: cost governance with three-tier quota and degradation."""

    async def check_quota(self, ctx: GuardContext) -> None:
        """Check all three tiers. Raises QuotaExceededError if any exceeded."""
        ...

    async def consume(
        self,
        ctx: GuardContext,
        tokens: int,
        cost_usd: float | None = None,
    ) -> None:
        """Atomically consume tokens across all three tiers."""
        ...

    async def reserve(
        self,
        ctx: GuardContext,
        tokens: int,
    ) -> None:
        """Reserve tokens upfront (for multi-agent collaboration total cost)."""
        ...

    async def release_reservation(
        self,
        ctx: GuardContext,
        tokens: int,
    ) -> None:
        """Release unused reservation."""
        ...

    async def degrade(
        self,
        ctx: GuardContext,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        """Degrade on quota exceedance: cheaper model -> cache -> queue -> reject."""
        ...

    async def get_status(
        self,
        tenant_id: UUID,
        scope: QuotaScope,
        owner_id: UUID | None,
    ) -> QuotaStatus:
        """Get current quota usage status."""
        ...


def _period_for_scope(scope: QuotaScope) -> str:
    """Default period: monthly for org/dept, daily for agent."""
    if scope == QuotaScope.AGENT:
        return "daily"
    return "monthly"


def _row_to_config(row: dict[str, Any], scope: QuotaScope) -> QuotaConfig:
    """Convert a DB row to a QuotaConfig."""
    return QuotaConfig(
        tenant_id=row["tenant_id"],
        scope=scope,
        owner_id=row.get("owner_id"),
        period=row.get("period", _period_for_scope(scope)),
        token_limit=int(row.get("token_limit", 0)),
        cost_limit_usd=(
            float(row["cost_limit_usd"]) if row.get("cost_limit_usd") is not None else None
        ),
    )


def _row_to_status(row: dict[str, Any], scope: QuotaScope) -> QuotaStatus:
    """Convert a DB row to a QuotaStatus."""
    config = _row_to_config(row, scope)
    token_used = int(row.get("token_used", 0))
    cost_used = float(row.get("cost_used_usd", 0))
    remaining_tokens = max(0, config.token_limit - token_used)
    remaining_cost = (
        max(0.0, config.cost_limit_usd - cost_used)
        if config.cost_limit_usd is not None
        else None
    )
    utilization = (token_used / config.token_limit * 100) if config.token_limit > 0 else 0.0
    return QuotaStatus(
        config=config,
        token_used=token_used,
        cost_used_usd=cost_used,
        remaining_tokens=remaining_tokens,
        remaining_cost_usd=remaining_cost,
        utilization_pct=round(utilization, 2),
    )


class CostGovernorImpl:
    """Concrete CostGovernor backed by PostgreSQL.

    Checks and updates ``harness.quotas`` across three tiers: organization
    (monthly), department (monthly), agent (daily). Missing quota rows are
    treated as unlimited (no quota configured).
    """

    def __init__(self, db: CostDb) -> None:
        self._db = db

    async def check_quota(self, ctx: GuardContext) -> None:
        """Check all three tiers. Raises QuotaExceededError if any exceeded."""
        tiers = self._tier_filters(ctx)
        for scope, owner_id in tiers:
            row = await self._fetch_quota(ctx.tenant_id, scope, owner_id)
            if row is None:
                continue  # no quota configured = unlimited
            status = _row_to_status(row, scope)
            if status.remaining_tokens <= 0:
                raise QuotaExceededError(
                    f"token quota exceeded for {scope.value}"
                    f"{'/' + str(owner_id) if owner_id else ''}: "
                    f"used {status.token_used}/{status.config.token_limit}"
                )
            if (
                status.config.cost_limit_usd is not None
                and status.remaining_cost_usd is not None
                and status.remaining_cost_usd <= 0
            ):
                raise QuotaExceededError(
                    f"cost quota exceeded for {scope.value}"
                    f"{'/' + str(owner_id) if owner_id else ''}: "
                    f"used ${status.cost_used_usd:.2f}/${status.config.cost_limit_usd:.2f}"
                )

    async def consume(
        self,
        ctx: GuardContext,
        tokens: int,
        cost_usd: float | None = None,
    ) -> None:
        """Atomically consume tokens across all three tiers."""
        tiers = self._tier_filters(ctx)
        for scope, owner_id in tiers:
            row = await self._fetch_quota(ctx.tenant_id, scope, owner_id)
            if row is None:
                continue
            await self._db.execute(
                """UPDATE harness.quotas
                   SET token_used = token_used + :p0,
                       cost_used_usd = cost_used_usd + :p1
                   WHERE tenant_id = :p2 AND scope = :p3
                     AND COALESCE(owner_id::text, '') = COALESCE(:p4::text, '')
                     AND period = :p5""",
                tokens,
                cost_usd or 0.0,
                ctx.tenant_id,
                scope.value,
                str(owner_id) if owner_id else None,
                row.get("period", _period_for_scope(scope)),
            )

    async def reserve(self, ctx: GuardContext, tokens: int) -> None:
        """Reserve tokens upfront (same as consume for this implementation)."""
        await self.consume(ctx, tokens)

    async def release_reservation(self, ctx: GuardContext, tokens: int) -> None:
        """Release unused reservation by decrementing token_used."""
        tiers = self._tier_filters(ctx)
        for scope, owner_id in tiers:
            row = await self._fetch_quota(ctx.tenant_id, scope, owner_id)
            if row is None:
                continue
            await self._db.execute(
                """UPDATE harness.quotas
                   SET token_used = GREATEST(0, token_used - :p0)
                   WHERE tenant_id = :p1 AND scope = :p2
                     AND COALESCE(owner_id::text, '') = COALESCE(:p3::text, '')
                     AND period = :p4""",
                tokens,
                ctx.tenant_id,
                scope.value,
                str(owner_id) if owner_id else None,
                row.get("period", _period_for_scope(scope)),
            )

    async def degrade(
        self,
        ctx: GuardContext,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        """Execute a degraded fallback function (caller provides the alternative)."""
        return func(*args, **kwargs)

    async def get_status(
        self,
        tenant_id: UUID,
        scope: QuotaScope,
        owner_id: UUID | None,
    ) -> QuotaStatus:
        """Get current quota usage status."""
        row = await self._fetch_quota(tenant_id, scope, owner_id)
        if row is None:
            return QuotaStatus(
                config=QuotaConfig(
                    tenant_id=tenant_id,
                    scope=scope,
                    owner_id=owner_id,
                    period=_period_for_scope(scope),
                    token_limit=0,
                ),
                token_used=0,
                cost_used_usd=0.0,
                remaining_tokens=0,
                remaining_cost_usd=None,
                utilization_pct=0.0,
            )
        return _row_to_status(row, scope)

    def _tier_filters(
        self, ctx: GuardContext
    ) -> list[tuple[QuotaScope, UUID | None]]:
        """Build (scope, owner_id) pairs for org, dept, agent tiers."""
        tiers: list[tuple[QuotaScope, UUID | None]] = [
            (QuotaScope.ORGANIZATION, None),
            (QuotaScope.AGENT, ctx.agent_id),
        ]
        if ctx.department_ids:
            tiers.append((QuotaScope.DEPARTMENT, ctx.department_ids[0]))
        return tiers

    async def _fetch_quota(
        self,
        tenant_id: UUID,
        scope: QuotaScope,
        owner_id: UUID | None,
    ) -> dict[str, Any] | None:
        """Fetch a quota row for a specific tier."""
        if owner_id is not None:
            return await self._db.fetch_one(
                """SELECT * FROM harness.quotas
                   WHERE tenant_id = :p0 AND scope = :p1 AND owner_id = :p2
                   AND period = :p3""",
                tenant_id,
                scope.value,
                owner_id,
                _period_for_scope(scope),
            )
        return await self._db.fetch_one(
            """SELECT * FROM harness.quotas
               WHERE tenant_id = :p0 AND scope = :p1 AND owner_id IS NULL
               AND period = :p2""",
            tenant_id,
            scope.value,
            _period_for_scope(scope),
        )
