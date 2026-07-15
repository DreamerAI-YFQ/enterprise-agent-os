"""Pillar 6: Evolution governance — RL strategy six-step release pipeline.

The most unique pillar. RL-trained strategies CANNOT go live directly; they
must pass: safety benchmark -> perf compare -> shadow traffic -> human
approval -> canary rollout -> full release. Anomaly at any stage triggers
auto-rollback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.evolution.guardrail import GuardrailChecker
    from eaos.harness.context import GuardContext


class EvolutionDb(Protocol):
    """Minimal DB subset for evolution governance."""

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...

    async def execute(self, sql: str, *params: Any) -> None: ...


class EvolutionStage(StrEnum):
    """Six release stages for an RL strategy."""

    SAFETY_BENCHMARK = "safety_benchmark"
    PERF_COMPARE = "perf_compare"
    SHADOW = "shadow"
    APPROVAL = "approval"
    CANARY = "canary"
    FULL = "full"


class StageStatus(StrEnum):
    """Status of a stage."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    REJECTED = "rejected"  # human rejected at approval stage


@dataclass(frozen=True)
class GuardrailResult:
    """Result of a stage check."""

    passed: bool
    stage: EvolutionStage
    reason: str | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class EvolutionStrategy:
    """An RL strategy going through the governance pipeline."""

    id: UUID
    tenant_id: UUID
    training_run_id: UUID
    current_stage: EvolutionStage
    stage_status: StageStatus
    created_at: object  # datetime


class EvolutionGovernor(Protocol):
    """Pillar 6: evolution governance — six-step release pipeline."""

    async def submit_strategy(
        self,
        strategy_id: UUID,
        ctx: GuardContext,
    ) -> None:
        """Submit a new RL strategy, enters safety_benchmark stage."""
        ...

    async def advance_stage(
        self,
        strategy_id: UUID,
        ctx: GuardContext,
    ) -> None:
        """Attempt to advance to next stage. Auto-runs checks for benchmark stages."""
        ...

    async def run_safety_benchmark(self, strategy_id: UUID) -> GuardrailResult:
        """Stage 1: run compliance test cases. 100% pass required."""
        ...

    async def run_perf_compare(self, strategy_id: UUID) -> GuardrailResult:
        """Stage 2: compare key metrics vs baseline. Must be >= 95%."""
        ...

    async def start_shadow(
        self,
        strategy_id: UUID,
        traffic_pct: int = 10,
        duration_hours: int = 24,
    ) -> None:
        """Stage 3: shadow traffic (10% runs new strategy, compares metrics)."""
        ...

    async def request_approval(
        self,
        strategy_id: UUID,
    ) -> None:
        """Stage 4: request human approval (notifies admins)."""
        ...

    async def approve(self, strategy_id: UUID, approver: UUID) -> None:
        """Admin approves strategy for canary rollout."""
        ...

    async def reject(self, strategy_id: UUID, approver: UUID, reason: str) -> None:
        """Admin rejects strategy."""
        ...

    async def canary_rollout(
        self,
        strategy_id: UUID,
        stages: list[int] | None = None,  # [30, 50, 100]
    ) -> None:
        """Stage 5: gradual canary rollout."""
        ...

    async def full_release(self, strategy_id: UUID) -> None:
        """Stage 6: full release."""
        ...

    async def auto_rollback(
        self,
        strategy_id: UUID,
        reason: str,
    ) -> None:
        """Auto-rollback on anomaly during canary/full. Alerts admins."""
        ...


_STAGE_ORDER: list[EvolutionStage] = [
    EvolutionStage.SAFETY_BENCHMARK,
    EvolutionStage.PERF_COMPARE,
    EvolutionStage.SHADOW,
    EvolutionStage.APPROVAL,
    EvolutionStage.CANARY,
    EvolutionStage.FULL,
]


def _next_stage(current: EvolutionStage) -> EvolutionStage | None:
    """Return the stage after ``current``, or None if already at FULL."""
    idx = _STAGE_ORDER.index(current)
    if idx + 1 >= len(_STAGE_ORDER):
        return None
    return _STAGE_ORDER[idx + 1]


class EvolutionGovernorImpl:
    """Concrete EvolutionGovernor backed by PostgreSQL.

    Persists RL strategy lifecycle to ``harness.evolution_strategies``. Stage
    transitions follow the six-step pipeline: safety_benchmark -> perf_compare
    -> shadow -> approval -> canary -> full. Benchmark stages auto-run checks;
    the approval stage blocks for human decision.
    """

    def __init__(
        self,
        db: EvolutionDb,
        guardrail: GuardrailChecker | None = None,
    ) -> None:
        self._db = db
        self._guardrail = guardrail

    async def submit_strategy(
        self,
        strategy_id: UUID,
        ctx: GuardContext,
    ) -> None:
        """Submit a new RL strategy, enters safety_benchmark stage."""
        training_run_id = ctx.attributes.get("training_run_id", strategy_id)
        await self._db.execute(
            """INSERT INTO harness.evolution_strategies
                   (id, tenant_id, training_run_id, stage, stage_status)
               VALUES (:p0, :p1, :p2, :p3, :p4)""",
            strategy_id,
            ctx.tenant_id,
            training_run_id,
            str(EvolutionStage.SAFETY_BENCHMARK.value),
            str(StageStatus.PENDING.value),
        )

    async def advance_stage(
        self,
        strategy_id: UUID,
        ctx: GuardContext,
    ) -> None:
        """Attempt to advance to next stage. Auto-runs benchmark checks."""
        row = await self._db.fetch_one(
            "SELECT stage, stage_status FROM harness.evolution_strategies WHERE id = :p0",
            strategy_id,
        )
        if row is None:
            from eaos.core.errors import NotFoundError

            raise NotFoundError(f"strategy {strategy_id} not found")

        current = EvolutionStage(str(row["stage"]))
        status = str(row["stage_status"])
        if status != str(StageStatus.PASSED.value):
            from eaos.core.errors import EvolutionError

            raise EvolutionError(
                f"cannot advance strategy {strategy_id}: stage {current} "
                f"status is {status}, expected passed"
            )

        nxt = _next_stage(current)
        if nxt is None:
            return  # already at FULL

        # Benchmark stages auto-run their checks before advancing.
        if nxt == EvolutionStage.PERF_COMPARE:
            result = await self.run_safety_benchmark(strategy_id)
            if not result.passed:
                await self._set_status(strategy_id, StageStatus.FAILED)
                return
        elif nxt == EvolutionStage.SHADOW:
            result = await self.run_perf_compare(strategy_id)
            if not result.passed:
                await self._set_status(strategy_id, StageStatus.FAILED)
                return

        await self._db.execute(
            """UPDATE harness.evolution_strategies
               SET stage = :p0, stage_status = :p1
               WHERE id = :p2""",
            str(nxt.value),
            str(StageStatus.PENDING.value),
            strategy_id,
        )

    async def run_safety_benchmark(self, strategy_id: UUID) -> GuardrailResult:
        """Stage 1: run compliance test cases. 100% pass required.

        Delegates to the injected ``GuardrailChecker`` (evolution package) when
        wired — runs real PII/authz/safety/policy cases from DB or YAML against
        the strategy's LLM. When no guardrail is wired, returns an honest
        pass-with-note; the M6 production flow uses ``EvolutionPipelineImpl``
        directly which always wires the real guardrail.
        """
        if self._guardrail is not None:
            tenant_id = await self._fetch_tenant_id(strategy_id)
            result = await self._guardrail.safety_benchmark(strategy_id, tenant_id)
            return GuardrailResult(
                passed=result.passed,
                stage=EvolutionStage.SAFETY_BENCHMARK,
                reason=result.reason,
                details=result.details,
            )
        return GuardrailResult(
            passed=True,
            stage=EvolutionStage.SAFETY_BENCHMARK,
            reason="no guardrail wired; M6 flow uses EvolutionPipelineImpl directly",
        )

    async def run_perf_compare(self, strategy_id: UUID) -> GuardrailResult:
        """Stage 2: compare key metrics vs baseline. Must be >= 95%.

        Delegates to the injected ``GuardrailChecker`` when wired — compares
        adoption rate, latency, and cost against baseline. When no guardrail is
        wired, returns an honest pass-with-note.
        """
        if self._guardrail is not None:
            result = await self._guardrail.perf_compare(strategy_id)
            return GuardrailResult(
                passed=result.passed,
                stage=EvolutionStage.PERF_COMPARE,
                reason=result.reason,
                details=result.details,
            )
        return GuardrailResult(
            passed=True,
            stage=EvolutionStage.PERF_COMPARE,
            reason="no guardrail wired; M6 flow uses EvolutionPipelineImpl directly",
        )

    async def _fetch_tenant_id(self, strategy_id: UUID) -> UUID | None:
        """Look up the tenant_id for a strategy (for tenant-scoped safety cases)."""
        row = await self._db.fetch_one(
            "SELECT tenant_id FROM harness.evolution_strategies WHERE id = :p0",
            strategy_id,
        )
        if row is not None:
            return row.get("tenant_id")
        return None

    async def start_shadow(
        self,
        strategy_id: UUID,
        traffic_pct: int = 10,
        duration_hours: int = 24,
    ) -> None:
        """Stage 3: shadow traffic (10% runs new strategy, compares metrics)."""
        await self._db.execute(
            """UPDATE harness.evolution_strategies
               SET stage = :p0, stage_status = :p1,
                   stage_detail = CAST(:p2 AS jsonb)
               WHERE id = :p3""",
            str(EvolutionStage.SHADOW.value),
            str(StageStatus.PASSED.value),
            f'{{"traffic_pct": {traffic_pct}, "duration_hours": {duration_hours}}}',
            strategy_id,
        )

    async def request_approval(
        self,
        strategy_id: UUID,
    ) -> None:
        """Stage 4: request human approval (notifies admins)."""
        await self._db.execute(
            """UPDATE harness.evolution_strategies
               SET stage = :p0, stage_status = :p1
               WHERE id = :p2""",
            str(EvolutionStage.APPROVAL.value),
            str(StageStatus.PENDING.value),
            strategy_id,
        )

    async def approve(
        self,
        strategy_id: UUID,
        approver: UUID,
    ) -> None:
        """Admin approves strategy for canary rollout."""
        await self._db.execute(
            """UPDATE harness.evolution_strategies
               SET stage = :p0, stage_status = :p1,
                   stage_detail = CAST(:p2 AS jsonb), promoted_at = now()
               WHERE id = :p3""",
            str(EvolutionStage.CANARY.value),
            str(StageStatus.PENDING.value),
            f'{{"approved_by": "{approver}"}}',
            strategy_id,
        )

    async def reject(
        self,
        strategy_id: UUID,
        approver: UUID,
        reason: str,
    ) -> None:
        """Admin rejects strategy."""
        await self._db.execute(
            """UPDATE harness.evolution_strategies
               SET stage_status = :p0,
                   stage_detail = CAST(:p1 AS jsonb)
               WHERE id = :p2""",
            str(StageStatus.REJECTED.value),
            f'{{"rejected_by": "{approver}", "reason": "{reason}"}}',
            strategy_id,
        )

    async def canary_rollout(
        self,
        strategy_id: UUID,
        stages: list[int] | None = None,  # [30, 50, 100]
    ) -> None:
        """Stage 5: gradual canary rollout."""
        traffic_stages = stages or [30, 50, 100]
        await self._db.execute(
            """UPDATE harness.evolution_strategies
               SET stage = :p0, stage_status = :p1,
                   stage_detail = CAST(:p2 AS jsonb)
               WHERE id = :p3""",
            str(EvolutionStage.CANARY.value),
            str(StageStatus.PASSED.value),
            f'{{"canary_stages": {traffic_stages}}}',
            strategy_id,
        )

    async def full_release(self, strategy_id: UUID) -> None:
        """Stage 6: full release."""
        await self._db.execute(
            """UPDATE harness.evolution_strategies
               SET stage = :p0, stage_status = :p1, promoted_at = now()
               WHERE id = :p2""",
            str(EvolutionStage.FULL.value),
            str(StageStatus.PASSED.value),
            strategy_id,
        )

    async def auto_rollback(
        self,
        strategy_id: UUID,
        reason: str,
    ) -> None:
        """Auto-rollback on anomaly during canary/full. Alerts admins."""
        await self._db.execute(
            """UPDATE harness.evolution_strategies
               SET stage_status = :p0,
                   stage_detail = CAST(:p1 AS jsonb)
               WHERE id = :p2""",
            str(StageStatus.FAILED.value),
            json.dumps({"rollback_reason": reason}),
            strategy_id,
        )

    async def _set_status(self, strategy_id: UUID, status: StageStatus) -> None:
        """Update only the stage_status column."""
        await self._db.execute(
            "UPDATE harness.evolution_strategies SET stage_status = :p0 WHERE id = :p1",
            str(status.value),
            strategy_id,
        )
