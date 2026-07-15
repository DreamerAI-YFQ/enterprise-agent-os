"""Trace query protocol and PostgreSQL implementation — five-level drill-down.

PgTraceQuery implements the TraceQuery protocol with SQL aggregations on
``trace.spans``. Each level drills deeper: company → department → agent →
skill → single trace span tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from eaos.observability.store import _row_to_span

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from eaos.observability.span import Span
    from eaos.observability.store import TraceDb


@dataclass(frozen=True)
class DateRange:
    """Inclusive date range for queries."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class Overview:
    """Top-level dashboard overview."""

    tenant_id: UUID
    total_agents: int
    active_users_today: int
    total_tokens_today: int
    total_cost_usd_today: float
    top_skills: list[dict[str, Any]] = field(default_factory=list)
    task_success_rate: float = 0.0


class TraceQuery(Protocol):
    """Five-level drill-down query interface for dashboards."""

    async def overview(
        self,
        tenant_id: UUID,
        date_range: DateRange,
    ) -> Overview:
        """Level 1: company-wide overview."""
        ...

    async def by_department(
        self,
        tenant_id: UUID,
        dept_id: UUID,
        date_range: DateRange,
    ) -> dict[str, Any]:
        """Level 2: department dashboard."""
        ...

    async def by_agent(
        self,
        agent_id: UUID,
        date_range: DateRange,
    ) -> dict[str, Any]:
        """Level 3: agent detail (call chain, memory r/w, task history)."""
        ...

    async def by_skill(
        self,
        skill_id: UUID,
        date_range: DateRange,
    ) -> dict[str, Any]:
        """Level 4: skill quality dashboard."""
        ...

    async def trace_detail(self, trace_id: UUID) -> list[Span]:
        """Level 5: single task full span tree (four granularities)."""
        ...


_SELECT_COLUMNS = (
    "id, tenant_id, trace_id, parent_span_id, agent_id, session_id, "
    "granularity, name, start_time, end_time, duration_ms, status, "
    "attributes, events, cost_tokens, cost_usd, user_id"
)


class PgTraceQuery:
    """PostgreSQL-backed TraceQuery implementing the TraceQuery protocol.

    All SQL uses ``:p0, :p1, ...`` named placeholders per DbClient convention.
    """

    def __init__(self, db: TraceDb) -> None:
        self._db = db

    async def overview(
        self,
        tenant_id: UUID,
        date_range: DateRange,
    ) -> Overview:
        """Level 1: company-wide overview with aggregates and top skills."""
        row = await self._db.fetch_one(
            """SELECT
                   COUNT(DISTINCT agent_id) AS total_agents,
                   COUNT(DISTINCT user_id) AS active_users_today,
                   COALESCE(SUM(cost_tokens), 0) AS total_tokens_today,
                   COALESCE(SUM(cost_usd), 0) AS total_cost_usd_today,
                   COUNT(*) FILTER (WHERE status = 'error') AS error_count,
                   COUNT(*) AS total_count
               FROM trace.spans
               WHERE tenant_id = :p0
                 AND start_time >= :p1 AND start_time <= :p2""",
            tenant_id,
            date_range.start,
            date_range.end,
        )

        if row is None:
            return Overview(
                tenant_id=tenant_id,
                total_agents=0,
                active_users_today=0,
                total_tokens_today=0,
                total_cost_usd_today=0.0,
            )

        total = row.get("total_count") or 0
        errors = row.get("error_count") or 0
        success_rate = (total - errors) / total if total > 0 else 0.0

        skill_rows = await self._db.fetch(
            """SELECT name, COUNT(*) AS count
               FROM trace.spans
               WHERE tenant_id = :p0 AND granularity = 'tool'
                 AND start_time >= :p1 AND start_time <= :p2
               GROUP BY name
               ORDER BY count DESC
               LIMIT 5""",
            tenant_id,
            date_range.start,
            date_range.end,
        )
        top_skills = [{"name": r["name"], "count": r["count"]} for r in skill_rows]

        return Overview(
            tenant_id=tenant_id,
            total_agents=row.get("total_agents") or 0,
            active_users_today=row.get("active_users_today") or 0,
            total_tokens_today=row.get("total_tokens_today") or 0,
            total_cost_usd_today=float(row.get("total_cost_usd_today") or 0.0),
            top_skills=top_skills,
            task_success_rate=round(success_rate, 4),
        )

    async def by_department(
        self,
        tenant_id: UUID,
        dept_id: UUID,
        date_range: DateRange,
    ) -> dict[str, Any]:
        """Level 2: department dashboard via join with iam.memberships."""
        row = await self._db.fetch_one(
            """SELECT
                   COUNT(DISTINCT s.agent_id) AS total_agents,
                   COUNT(DISTINCT s.user_id) AS active_users,
                   COALESCE(SUM(s.cost_tokens), 0) AS total_tokens,
                   COALESCE(SUM(s.cost_usd), 0) AS total_cost,
                   COUNT(*) FILTER (WHERE s.status = 'error') AS error_count,
                   COUNT(*) AS total_count
               FROM trace.spans s
               JOIN iam.memberships m
                 ON m.user_id = s.user_id AND m.tenant_id = s.tenant_id
               WHERE s.tenant_id = :p0 AND m.department_id = :p1
                 AND s.start_time >= :p2 AND s.start_time <= :p3""",
            tenant_id,
            dept_id,
            date_range.start,
            date_range.end,
        )

        if row is None:
            return {"department_id": str(dept_id), "total_calls": 0}

        total = row.get("total_count") or 0
        errors = row.get("error_count") or 0
        success_rate = (total - errors) / total if total > 0 else 0.0

        return {
            "department_id": str(dept_id),
            "total_agents": row.get("total_agents") or 0,
            "active_users": row.get("active_users") or 0,
            "total_tokens": row.get("total_tokens") or 0,
            "total_cost": float(row.get("total_cost") or 0.0),
            "total_calls": total,
            "error_count": errors,
            "success_rate": round(success_rate, 4),
        }

    async def by_agent(
        self,
        agent_id: UUID,
        date_range: DateRange,
    ) -> dict[str, Any]:
        """Level 3: agent detail with per-granularity breakdown."""
        rows = await self._db.fetch(
            """SELECT
                   granularity,
                   COUNT(*) AS count,
                   COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                   COALESCE(SUM(cost_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cost_usd), 0) AS total_cost,
                   COUNT(*) FILTER (WHERE status = 'error') AS error_count
               FROM trace.spans
               WHERE agent_id = :p0
                 AND start_time >= :p1 AND start_time <= :p2
               GROUP BY granularity""",
            agent_id,
            date_range.start,
            date_range.end,
        )

        granularities: dict[str, dict[str, Any]] = {}
        total_calls = 0
        total_tokens = 0
        total_errors = 0

        for r in rows:
            g = r["granularity"]
            count = r.get("count") or 0
            total_calls += count
            total_tokens += r.get("total_tokens") or 0
            total_errors += r.get("error_count") or 0
            granularities[g] = {
                "count": count,
                "avg_duration_ms": float(r.get("avg_duration_ms") or 0.0),
                "total_tokens": r.get("total_tokens") or 0,
                "total_cost": float(r.get("total_cost") or 0.0),
                "error_count": r.get("error_count") or 0,
            }

        success_rate = (
            (total_calls - total_errors) / total_calls if total_calls > 0 else 0.0
        )

        return {
            "agent_id": str(agent_id),
            "granularities": granularities,
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "error_count": total_errors,
            "success_rate": round(success_rate, 4),
        }

    async def by_skill(
        self,
        skill_id: UUID,
        date_range: DateRange,
    ) -> dict[str, Any]:
        """Level 4: skill quality dashboard (spans where name matches skill)."""
        row = await self._db.fetch_one(
            """SELECT
                   COUNT(*) AS total_calls,
                   COALESCE(AVG(duration_ms), 0) AS avg_duration_ms,
                   COUNT(*) FILTER (WHERE status = 'error') AS error_count,
                   COALESCE(SUM(cost_tokens), 0) AS total_tokens
               FROM trace.spans
               WHERE name = :p0 AND granularity = 'tool'
                 AND start_time >= :p1 AND start_time <= :p2""",
            str(skill_id),
            date_range.start,
            date_range.end,
        )

        if row is None:
            return {"skill_id": str(skill_id), "total_calls": 0}

        total = row.get("total_calls") or 0
        errors = row.get("error_count") or 0
        success_rate = (total - errors) / total if total > 0 else 0.0

        return {
            "skill_id": str(skill_id),
            "total_calls": total,
            "avg_duration_ms": float(row.get("avg_duration_ms") or 0.0),
            "error_count": errors,
            "total_tokens": row.get("total_tokens") or 0,
            "success_rate": round(success_rate, 4),
        }

    async def trace_detail(self, trace_id: UUID) -> list[Span]:
        """Level 5: full span tree for a single trace, ordered by start_time."""
        rows = await self._db.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM trace.spans "
            "WHERE trace_id = :p0 ORDER BY start_time ASC",
            trace_id,
        )
        return [_row_to_span(r) for r in rows]
