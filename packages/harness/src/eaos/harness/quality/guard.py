"""Pillar 5: Quality guardrail — skill auto-deprecation, hallucination detection.

Monitors skill failure rates (auto-deprecate > 30%), detects hallucinations in
critical scenes (cross-validate output against knowledge base), tracks adoption
rates (user modified > 70% = low quality).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from eaos.core.errors import QualityViolationError

if TYPE_CHECKING:
    from eaos.harness.context import GuardContext


@dataclass(frozen=True)
class QualityMetrics:
    """Quality metrics for a skill or agent output."""

    skill_id: UUID | None
    failure_rate: float
    adoption_rate: float | None
    avg_latency_ms: int | None
    hallucination_confidence: float | None


class QualityDb(Protocol):
    """Minimal DB subset for quality guard."""

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...

    async def execute(self, sql: str, *params: Any) -> None: ...


class QualityGuard(Protocol):
    """Pillar 5: quality guardrails."""

    async def evaluate(
        self,
        ctx: GuardContext,
        result: Any,
    ) -> None:
        """Post-action quality evaluation. May raise QualityViolationError."""
        ...

    async def check_hallucination(
        self,
        ctx: GuardContext,
        output: str,
    ) -> bool:
        """Cross-validate output against knowledge base for critical scenes."""
        ...

    async def check_skill_quality(
        self,
        skill_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """Check if skill should be auto-deprecated (failure rate > 30%)."""
        ...

    async def record_adoption(
        self,
        ctx: GuardContext,
        output: str,
        user_modified_pct: float,
    ) -> None:
        """Record how much the user modified the agent's output."""
        ...


_DEPRECATION_THRESHOLD = 0.30
_MIN_SAMPLES_FOR_GATE = 10


def _is_failure(result: Any) -> bool:
    """Determine if a result indicates failure."""
    if isinstance(result, Exception):
        return True
    if isinstance(result, dict):
        status = result.get("status")
        if status in ("error", "failed"):
            return True
    return isinstance(result, str) and result.lower().startswith("error:")


class QualityGuardImpl:
    """Concrete QualityGuard backed by PostgreSQL.

    Records skill call metrics (success/failure) to ``harness.quality_metrics``
    and gates skills whose failure rate exceeds 30% (with minimum 10 samples).
    Hallucination detection is a stub (always passes) in Phase 4; production
    would cross-validate against a knowledge base.
    """

    def __init__(self, db: QualityDb) -> None:
        self._db = db

    async def evaluate(
        self,
        ctx: GuardContext,
        result: Any,
    ) -> None:
        """Record metric and check skill quality. Raises on deprecation."""
        skill_id = ctx.resource_id or ctx.attributes.get("skill_id")
        if skill_id is None:
            return  # no skill to track

        skill_uuid = skill_id if isinstance(skill_id, UUID) else _to_uuid(skill_id)
        is_failure = _is_failure(result)
        today = datetime.now(UTC).date()

        await self._db.execute(
            """INSERT INTO harness.quality_metrics
                   (tenant_id, skill_id, metric_date, call_count,
                    success_count, failure_count)
               VALUES (:p0, :p1, :p2, 1, :p3, :p4)
               ON CONFLICT (tenant_id, skill_id, metric_date)
               DO UPDATE SET
                   call_count = harness.quality_metrics.call_count + 1,
                   success_count = harness.quality_metrics.success_count + :p3,
                   failure_count = harness.quality_metrics.failure_count + :p4""",
            ctx.tenant_id,
            skill_uuid,
            today,
            0 if is_failure else 1,
            1 if is_failure else 0,
        )

        if not await self.check_skill_quality(skill_uuid, ctx.tenant_id):
            raise QualityViolationError(
                f"skill {skill_uuid} deprecated: failure rate exceeds "
                f"{int(_DEPRECATION_THRESHOLD * 100)}%"
            )

    async def check_hallucination(
        self,
        ctx: GuardContext,
        output: str,
    ) -> bool:
        """Cross-validate output. Phase 4 stub: always returns True."""
        return True

    async def check_skill_quality(
        self,
        skill_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """Check if skill is healthy (failure rate < 30% with enough samples)."""
        row = await self._db.fetch_one(
            """SELECT call_count, failure_count FROM harness.quality_metrics
               WHERE tenant_id = :p0 AND skill_id = :p1
               ORDER BY metric_date DESC LIMIT 1""",
            tenant_id,
            skill_id,
        )
        if row is None:
            return True  # no data = healthy

        call_count = int(row.get("call_count", 0))
        if call_count < _MIN_SAMPLES_FOR_GATE:
            return True  # not enough samples to gate

        failure_count = int(row.get("failure_count", 0))
        failure_rate = failure_count / call_count
        return failure_rate < _DEPRECATION_THRESHOLD

    async def record_adoption(
        self,
        ctx: GuardContext,
        output: str,
        user_modified_pct: float,
    ) -> None:
        """Record adoption rate for the skill's latest metrics."""
        skill_id = ctx.resource_id or ctx.attributes.get("skill_id")
        if skill_id is None:
            return

        skill_uuid = skill_id if isinstance(skill_id, UUID) else _to_uuid(skill_id)
        today = datetime.now(UTC).date()

        await self._db.execute(
            """UPDATE harness.quality_metrics
               SET adoption_rate = :p0
               WHERE tenant_id = :p1 AND skill_id = :p2 AND metric_date = :p3""",
            user_modified_pct,
            ctx.tenant_id,
            skill_uuid,
            today,
        )


def _to_uuid(value: Any) -> UUID:
    """Convert a string to UUID."""
    return value if isinstance(value, UUID) else UUID(str(value))
