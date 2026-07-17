"""Skill quality monitor protocol — track success rate, auto-deprecate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient
    from eaos.skills.registry import SkillRegistry


@dataclass(frozen=True)
class SkillQualityMetrics:
    """Quality metrics for a skill (sliding window)."""

    skill_id: UUID
    call_count: int
    success_count: int
    failure_count: int
    failure_rate: float
    adoption_rate: float | None
    avg_latency_ms: int | None


class SkillQualityMonitor(Protocol):
    """Monitor skill quality, trigger auto-deprecation on high failure rate."""

    async def record(
        self,
        skill_id: UUID,
        tenant_id: UUID,
        success: bool,
        latency_ms: int,
    ) -> None:
        """Record a skill invocation outcome."""
        ...

    async def get_metrics(
        self,
        skill_id: UUID,
        tenant_id: UUID,
        window_hours: int = 24,
    ) -> SkillQualityMetrics:
        """Compute quality metrics over a sliding window."""
        ...

    async def check_auto_deprecate(
        self,
        skill_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """Check if failure rate exceeds threshold (30%), trigger deprecation."""
        ...


_FAILURE_RATE_THRESHOLD = 0.3


class PgSkillQualityMonitor:
    """SkillQualityMonitor backed by harness.quality_metrics (daily rollup).

    The underlying table aggregates per (tenant, skill, day). ``record`` upserts
    a daily row incrementing counts; ``get_metrics`` sums across the window.
    """

    def __init__(self, db: DbClient, registry: SkillRegistry) -> None:
        self._db = db
        self._registry = registry

    async def record(
        self,
        skill_id: UUID,
        tenant_id: UUID,
        success: bool,
        latency_ms: int,
    ) -> None:
        success_inc = 1 if success else 0
        failure_inc = 0 if success else 1
        await self._db.execute(
            "INSERT INTO harness.quality_metrics "
            "(id, tenant_id, skill_id, metric_date, call_count, "
            " success_count, failure_count, avg_latency_ms) "
            "VALUES (gen_random_uuid(), :p0, :p1, CURRENT_DATE, 1, :p2, :p3, :p4) "
            "ON CONFLICT (tenant_id, skill_id, metric_date) DO UPDATE SET "
            "  call_count = harness.quality_metrics.call_count + 1, "
            "  success_count = harness.quality_metrics.success_count + :p2, "
            "  failure_count = harness.quality_metrics.failure_count + :p3, "
            "  avg_latency_ms = :p4",
            tenant_id,
            skill_id,
            success_inc,
            failure_inc,
            latency_ms,
        )

    async def get_metrics(
        self,
        skill_id: UUID,
        tenant_id: UUID,
        window_hours: int = 24,
    ) -> SkillQualityMetrics:
        rows = await self._db.fetch(
            "SELECT COALESCE(SUM(call_count), 0) AS total_calls, "
            "COALESCE(SUM(success_count), 0) AS total_success, "
            "COALESCE(SUM(failure_count), 0) AS total_failure, "
            "AVG(avg_latency_ms) AS avg_latency "
            "FROM harness.quality_metrics "
            "WHERE tenant_id = :p0 AND skill_id = :p1 "
            "AND metric_date >= CURRENT_DATE - (:p2 || ' hours')::interval",
            tenant_id,
            skill_id,
            str(window_hours),
        )
        row = rows[0] if rows else {}
        total_calls = int(row.get("total_calls") or 0)
        total_success = int(row.get("total_success") or 0)
        total_failure = int(row.get("total_failure") or 0)
        failure_rate = (total_failure / total_calls) if total_calls > 0 else 0.0
        avg_latency_raw = row.get("avg_latency")
        avg_latency = int(avg_latency_raw) if avg_latency_raw is not None else None
        return SkillQualityMetrics(
            skill_id=skill_id,
            call_count=total_calls,
            success_count=total_success,
            failure_count=total_failure,
            failure_rate=failure_rate,
            adoption_rate=None,
            avg_latency_ms=avg_latency,
        )

    async def check_auto_deprecate(
        self,
        skill_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        metrics = await self.get_metrics(skill_id, tenant_id)
        if metrics.call_count > 0 and metrics.failure_rate > _FAILURE_RATE_THRESHOLD:
            await self._registry.deprecate(
                skill_id, tenant_id, f"auto-deprecate: failure_rate={metrics.failure_rate:.2f}"
            )
            return True
        return False
