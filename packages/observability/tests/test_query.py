"""Tests for PgTraceQuery — five-level drill-down queries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from eaos.observability.query import DateRange, Overview, PgTraceQuery
from eaos.observability.span import Granularity, Span


class FakeTraceDb:
    """In-memory TraceDb with configurable row sets per call index."""

    def __init__(
        self,
        fetch_one_rows: list[dict[str, Any] | None] | None = None,
        fetch_rows: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.fetch_one_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self._fetch_one_rows = fetch_one_rows or []
        self._fetch_rows = fetch_rows or []
        self._fetch_one_idx = 0
        self._fetch_idx = 0

    async def execute(self, sql: str, *params: Any) -> None:
        pass

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((sql, params))
        if self._fetch_idx < len(self._fetch_rows):
            rows = self._fetch_rows[self._fetch_idx]
            self._fetch_idx += 1
            return list(rows)
        return []

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        self.fetch_one_calls.append((sql, params))
        if self._fetch_one_idx < len(self._fetch_one_rows):
            row = self._fetch_one_rows[self._fetch_one_idx]
            self._fetch_one_idx += 1
            return dict(row) if row is not None else None
        return None


def _date_range() -> DateRange:
    return DateRange(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 31, tzinfo=UTC),
    )


def _make_span_row(
    name: str = "test-span",
    granularity: Granularity = Granularity.TASK,
    trace_id: Any = None,
) -> dict[str, Any]:
    span = Span(
        name=name,
        tenant_id=uuid4(),
        trace_id=trace_id or uuid4(),
        agent_id=uuid4(),
        granularity=granularity,
        start_time=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
    )
    return {
        "id": span.id,
        "tenant_id": span.tenant_id,
        "trace_id": span.trace_id,
        "parent_span_id": span.parent_span_id,
        "agent_id": span.agent_id,
        "session_id": span.session_id,
        "granularity": span.granularity.value,
        "name": span.name,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "duration_ms": span.duration_ms,
        "status": span.status,
        "attributes": span.attributes,
        "events": [],
        "cost_tokens": span.cost_tokens,
        "cost_usd": span.cost_usd,
        "user_id": span.user_id,
    }


class TestOverview:
    async def test_returns_overview_dataclass(self) -> None:
        db = FakeTraceDb(
            fetch_one_rows=[
                {
                    "total_agents": 5,
                    "active_users_today": 12,
                    "total_tokens_today": 50000,
                    "total_cost_usd_today": 1.23,
                    "error_count": 2,
                    "total_count": 10,
                }
            ],
            fetch_rows=[[]],
        )
        query = PgTraceQuery(db)
        tenant = uuid4()

        result = await query.overview(tenant, _date_range())

        assert isinstance(result, Overview)
        assert result.tenant_id == tenant
        assert result.total_agents == 5
        assert result.active_users_today == 12
        assert result.total_tokens_today == 50000
        assert result.total_cost_usd_today == 1.23
        assert result.task_success_rate == 0.8  # (10-2)/10

    async def test_returns_empty_overview_when_no_rows(self) -> None:
        db = FakeTraceDb(fetch_one_rows=[None], fetch_rows=[[]])
        query = PgTraceQuery(db)
        tenant = uuid4()

        result = await query.overview(tenant, _date_range())

        assert result.total_agents == 0
        assert result.active_users_today == 0
        assert result.task_success_rate == 0.0

    async def test_top_skills_aggregated(self) -> None:
        db = FakeTraceDb(
            fetch_one_rows=[
                {
                    "total_agents": 1,
                    "active_users_today": 1,
                    "total_tokens_today": 100,
                    "total_cost_usd_today": 0.1,
                    "error_count": 0,
                    "total_count": 5,
                }
            ],
            fetch_rows=[
                [
                    {"name": "rag_search", "count": 8},
                    {"name": "text2sql", "count": 3},
                ]
            ],
        )
        query = PgTraceQuery(db)

        result = await query.overview(uuid4(), _date_range())

        assert len(result.top_skills) == 2
        assert result.top_skills[0] == {"name": "rag_search", "count": 8}
        assert result.top_skills[1] == {"name": "text2sql", "count": 3}

    async def test_overview_passes_tenant_and_date_params(self) -> None:
        db = FakeTraceDb(fetch_one_rows=[{"total_count": 0}], fetch_rows=[[]])
        query = PgTraceQuery(db)
        tenant = uuid4()
        dr = _date_range()

        await query.overview(tenant, dr)

        sql, params = db.fetch_one_calls[0]
        assert "tenant_id = :p0" in sql
        assert tenant in params
        assert dr.start in params
        assert dr.end in params


class TestByDepartment:
    async def test_returns_department_aggregates(self) -> None:
        db = FakeTraceDb(
            fetch_one_rows=[
                {
                    "total_agents": 3,
                    "active_users": 7,
                    "total_tokens": 20000,
                    "total_cost": 2.5,
                    "error_count": 1,
                    "total_count": 8,
                }
            ]
        )
        query = PgTraceQuery(db)
        tenant = uuid4()
        dept = uuid4()

        result = await query.by_department(tenant, dept, _date_range())

        assert result["department_id"] == str(dept)
        assert result["total_agents"] == 3
        assert result["active_users"] == 7
        assert result["total_tokens"] == 20000
        assert result["total_cost"] == 2.5
        assert result["success_rate"] == 0.875  # (8-1)/8

    async def test_returns_minimal_when_no_data(self) -> None:
        db = FakeTraceDb(fetch_one_rows=[None])
        query = PgTraceQuery(db)

        result = await query.by_department(uuid4(), uuid4(), _date_range())

        assert result["total_calls"] == 0

    async def test_joins_memberships_table(self) -> None:
        db = FakeTraceDb(
            fetch_one_rows=[
                {
                    "total_agents": 1,
                    "active_users": 1,
                    "total_tokens": 0,
                    "total_cost": 0.0,
                    "error_count": 0,
                    "total_count": 0,
                }
            ]
        )
        query = PgTraceQuery(db)

        await query.by_department(uuid4(), uuid4(), _date_range())

        sql, _ = db.fetch_one_calls[0]
        assert "iam.memberships" in sql
        assert "department_id" in sql


class TestByAgent:
    async def test_returns_per_granularity_breakdown(self) -> None:
        db = FakeTraceDb(
            fetch_rows=[
                [
                    {
                        "granularity": "task",
                        "count": 10,
                        "avg_duration_ms": 1500.0,
                        "total_tokens": 5000,
                        "total_cost": 0.5,
                        "error_count": 1,
                    },
                    {
                        "granularity": "call",
                        "count": 50,
                        "avg_duration_ms": 200.0,
                        "total_tokens": 30000,
                        "total_cost": 3.0,
                        "error_count": 2,
                    },
                ]
            ]
        )
        query = PgTraceQuery(db)
        agent = uuid4()

        result = await query.by_agent(agent, _date_range())

        assert result["agent_id"] == str(agent)
        assert result["total_calls"] == 60
        assert result["total_tokens"] == 35000
        assert result["error_count"] == 3
        assert "task" in result["granularities"]
        assert "call" in result["granularities"]
        assert result["granularities"]["task"]["count"] == 10
        assert result["granularities"]["call"]["avg_duration_ms"] == 200.0

    async def test_empty_when_no_spans(self) -> None:
        db = FakeTraceDb(fetch_rows=[[]])
        query = PgTraceQuery(db)

        result = await query.by_agent(uuid4(), _date_range())

        assert result["total_calls"] == 0
        assert result["success_rate"] == 0.0
        assert result["granularities"] == {}

    async def test_success_rate_calculation(self) -> None:
        db = FakeTraceDb(
            fetch_rows=[
                [
                    {
                        "granularity": "task",
                        "count": 4,
                        "avg_duration_ms": 100.0,
                        "total_tokens": 0,
                        "total_cost": 0.0,
                        "error_count": 1,
                    }
                ]
            ]
        )
        query = PgTraceQuery(db)

        result = await query.by_agent(uuid4(), _date_range())

        assert result["success_rate"] == 0.75  # (4-1)/4


class TestBySkill:
    async def test_returns_skill_metrics(self) -> None:
        db = FakeTraceDb(
            fetch_one_rows=[
                {
                    "total_calls": 25,
                    "avg_duration_ms": 800.0,
                    "error_count": 3,
                    "total_tokens": 12000,
                }
            ]
        )
        query = PgTraceQuery(db)
        skill = uuid4()

        result = await query.by_skill(skill, _date_range())

        assert result["skill_id"] == str(skill)
        assert result["total_calls"] == 25
        assert result["avg_duration_ms"] == 800.0
        assert result["error_count"] == 3
        assert result["total_tokens"] == 12000
        assert result["success_rate"] == 0.88  # (25-3)/25

    async def test_empty_when_no_skill_data(self) -> None:
        db = FakeTraceDb(fetch_one_rows=[None])
        query = PgTraceQuery(db)

        result = await query.by_skill(uuid4(), _date_range())

        assert result["total_calls"] == 0

    async def test_filters_by_tool_granularity(self) -> None:
        db = FakeTraceDb(
            fetch_one_rows=[
                {
                    "total_calls": 1,
                    "avg_duration_ms": 0.0,
                    "error_count": 0,
                    "total_tokens": 0,
                }
            ]
        )
        query = PgTraceQuery(db)

        await query.by_skill(uuid4(), _date_range())

        sql, _ = db.fetch_one_calls[0]
        assert "granularity = 'tool'" in sql


class TestTraceDetail:
    async def test_returns_span_list_ordered(self) -> None:
        trace = uuid4()
        rows = [
            _make_span_row("first", trace_id=trace),
            _make_span_row("second", trace_id=trace),
        ]
        db = FakeTraceDb(fetch_rows=[rows])
        query = PgTraceQuery(db)

        result = await query.trace_detail(trace)

        assert len(result) == 2
        assert all(s.trace_id == trace for s in result)
        assert result[0].name == "first"
        assert result[1].name == "second"

    async def test_empty_when_trace_not_found(self) -> None:
        db = FakeTraceDb(fetch_rows=[[]])
        query = PgTraceQuery(db)

        result = await query.trace_detail(uuid4())

        assert result == []

    async def test_orders_by_start_time(self) -> None:
        db = FakeTraceDb(fetch_rows=[[]])
        query = PgTraceQuery(db)

        await query.trace_detail(uuid4())

        sql, _ = db.fetch_calls[0]
        assert "ORDER BY start_time ASC" in sql
        assert "trace_id = :p0" in sql

    async def test_preserves_granularity_enum(self) -> None:
        trace = uuid4()
        rows = [_make_span_row("tool-call", Granularity.TOOL, trace_id=trace)]
        db = FakeTraceDb(fetch_rows=[rows])
        query = PgTraceQuery(db)

        result = await query.trace_detail(trace)

        assert result[0].granularity == Granularity.TOOL
