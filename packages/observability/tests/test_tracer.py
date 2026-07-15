"""Tests for PgTraceStore, TracerImpl, and @traced decorator wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from eaos.core.context import TenantContext
from eaos.observability._global import get_global_tracer, set_global_tracer
from eaos.observability.decorators import traced
from eaos.observability.span import Granularity, Span
from eaos.observability.store import PgTraceStore
from eaos.observability.tracer import TracerImpl


class FakeTraceDb:
    """In-memory TraceDb for recording execute/fetch calls."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetched: list[tuple[str, tuple[Any, ...]]] = []
        self.rows: list[dict[str, Any]] = rows or []

    async def execute(self, sql: str, *params: Any) -> None:
        self.executed.append((sql, params))

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.fetched.append((sql, params))
        return list(self.rows)

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        self.fetched.append((sql, params))
        return self.rows[0] if self.rows else None


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
    )


def _make_row(span: Span | None = None) -> dict[str, Any]:
    """Create a DB row dict that _row_to_span can parse."""
    if span is None:
        span = Span(
            tenant_id=uuid4(),
            trace_id=uuid4(),
            agent_id=uuid4(),
            granularity=Granularity.TASK,
            name="test-span",
            start_time=datetime.now(UTC),
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
        "events": [
            {"name": e.name, "timestamp": e.timestamp.isoformat(), "attributes": e.attributes}
            for e in span.events
        ],
        "cost_tokens": span.cost_tokens,
        "cost_usd": span.cost_usd,
        "user_id": span.user_id,
    }


class TestPgTraceStore:
    async def test_start_inserts_row(self) -> None:
        db = FakeTraceDb()
        store = PgTraceStore(db)
        span = Span(name="test-span", tenant_id=uuid4(), trace_id=uuid4(), agent_id=uuid4())

        await store.start(span)

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "INSERT INTO trace.spans" in sql
        assert span.id in params
        assert span.tenant_id in params
        assert span.name in params

    async def test_end_updates_row(self) -> None:
        db = FakeTraceDb()
        store = PgTraceStore(db)
        span = Span(name="test-span", tenant_id=uuid4(), trace_id=uuid4(), agent_id=uuid4())
        span.end_time = datetime.now(UTC)
        span.duration_ms = 42
        span.status = "ok"

        await store.end(span)

        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "UPDATE trace.spans" in sql
        assert "end_time" in sql
        assert "duration_ms" in sql
        assert span.id in params
        assert span.tenant_id in params

    async def test_get_returns_span(self) -> None:
        original = Span(
            name="fetched",
            tenant_id=uuid4(),
            trace_id=uuid4(),
            agent_id=uuid4(),
            granularity=Granularity.TOOL,
        )
        db = FakeTraceDb(rows=[_make_row(original)])
        store = PgTraceStore(db)

        result = await store.get(original.id, original.tenant_id)

        assert result.id == original.id
        assert result.name == "fetched"
        assert result.granularity == Granularity.TOOL

    async def test_get_raises_keyerror_when_not_found(self) -> None:
        db = FakeTraceDb(rows=[])
        store = PgTraceStore(db)

        with pytest.raises(KeyError):
            await store.get(uuid4(), uuid4())

    async def test_query_with_filters(self) -> None:
        db = FakeTraceDb(rows=[])
        store = PgTraceStore(db)
        tenant = uuid4()
        agent = uuid4()

        await store.query(
            tenant,
            {"agent_id": agent, "granularity": "call", "status": "error"},
            limit=10,
        )

        assert len(db.fetched) == 1
        sql, params = db.fetched[0]
        assert "tenant_id" in sql
        assert "agent_id" in sql
        assert "granularity" in sql
        assert "status" in sql
        assert "LIMIT" in sql
        assert tenant in params
        assert agent in params
        assert "call" in params
        assert "error" in params
        assert 10 in params

    async def test_query_with_granularity_enum(self) -> None:
        db = FakeTraceDb(rows=[])
        store = PgTraceStore(db)

        await store.query(uuid4(), {"granularity": Granularity.TASK})

        assert len(db.fetched) == 1
        _, params = db.fetched[0]
        assert "task" in params

    async def test_get_trace_returns_ordered_spans(self) -> None:
        trace = uuid4()
        row1 = _make_row(Span(name="first", trace_id=trace, tenant_id=uuid4(), agent_id=uuid4()))
        row2 = _make_row(Span(name="second", trace_id=trace, tenant_id=uuid4(), agent_id=uuid4()))
        db = FakeTraceDb(rows=[row1, row2])
        store = PgTraceStore(db)

        result = await store.get_trace(trace)

        assert len(result) == 2
        assert all(s.trace_id == trace for s in result)
        assert len(db.fetched) == 1
        sql, params = db.fetched[0]
        assert "trace_id = :p0" in sql
        assert trace in params


class TestTracerImpl:
    async def test_span_creates_and_ends(self) -> None:
        store = AsyncMock()
        tracer = TracerImpl(store)
        ctx = _ctx()

        cm = asynccontextmanager(tracer.span)
        async with cm("test-op", Granularity.TASK, ctx) as handle:
            assert handle.span.name == "test-op"
            assert handle.span.tenant_id == ctx.tenant_id
            assert handle.span.status == "ok"

        store.start.assert_awaited_once()
        store.end.assert_awaited_once()
        ended_span = store.end.call_args.args[0]
        assert ended_span.end_time is not None
        assert ended_span.duration_ms is not None
        assert ended_span.duration_ms >= 0

    async def test_span_sets_error_status_on_exception(self) -> None:
        store = AsyncMock()
        tracer = TracerImpl(store)
        ctx = _ctx()

        cm = asynccontextmanager(tracer.span)
        with pytest.raises(ValueError):
            async with cm("failing-op", Granularity.CALL, ctx):
                raise ValueError("boom")

        store.end.assert_awaited_once()
        ended_span = store.end.call_args.args[0]
        assert ended_span.status == "error"

    async def test_nested_spans_share_trace_id(self) -> None:
        store = AsyncMock()
        tracer = TracerImpl(store)
        ctx = _ctx()

        cm = asynccontextmanager(tracer.span)
        async with cm("parent", Granularity.TASK, ctx) as parent_handle:  # noqa: SIM117
            async with cm("child", Granularity.CALL, ctx) as child_handle:
                assert child_handle.span.trace_id == parent_handle.span.trace_id
                assert child_handle.span.parent_span_id == parent_handle.span.id

        assert store.start.await_count == 2
        assert store.end.await_count == 2

    async def test_current_trace_id_none_initially(self) -> None:
        tracer = TracerImpl(AsyncMock())
        assert await tracer.current_trace_id() is None
        assert await tracer.current_span_id() is None

    async def test_current_trace_id_set_during_span(self) -> None:
        store = AsyncMock()
        tracer = TracerImpl(store)
        ctx = _ctx()

        cm = asynccontextmanager(tracer.span)
        async with cm("active", Granularity.TASK, ctx) as handle:
            assert await tracer.current_trace_id() == handle.span.trace_id
            assert await tracer.current_span_id() == handle.span.id

        assert await tracer.current_trace_id() is None
        assert await tracer.current_span_id() is None

    async def test_span_passes_attributes(self) -> None:
        store = AsyncMock()
        tracer = TracerImpl(store)
        ctx = _ctx()

        cm = asynccontextmanager(tracer.span)
        async with cm("attr-test", Granularity.TOOL, ctx, custom_key="val", count=42) as handle:
            assert handle.span.attributes["custom_key"] == "val"
            assert handle.span.attributes["count"] == 42


class TestTracedDecorator:
    async def test_no_tracer_passthrough(self) -> None:
        set_global_tracer(None)

        @traced
        async def my_func(x: int) -> int:
            return x * 2

        assert await my_func(5) == 10

    async def test_no_tracer_passthrough_with_kwargs(self) -> None:
        set_global_tracer(None)

        @traced(name="custom", granularity=Granularity.CALL)
        async def my_func(x: int) -> int:
            return x + 1

        assert await my_func(10) == 11

    async def test_with_tracer_success(self) -> None:
        store = AsyncMock()
        tracer = TracerImpl(store)
        set_global_tracer(tracer)
        try:
            @traced
            async def my_func(x: int) -> int:
                return x * 3

            result = await my_func(4)
            assert result == 12

            store.start.assert_awaited_once()
            store.end.assert_awaited_once()
            ended_span = store.end.call_args.args[0]
            assert ended_span.status == "ok"
        finally:
            set_global_tracer(None)

    async def test_with_tracer_exception_sets_error(self) -> None:
        store = AsyncMock()
        tracer = TracerImpl(store)
        set_global_tracer(tracer)
        try:
            @traced
            async def failing_func() -> None:
                raise ValueError("kaboom")

            with pytest.raises(ValueError, match="kaboom"):
                await failing_func()

            store.start.assert_awaited_once()
            store.end.assert_awaited_once()
            ended_span = store.end.call_args.args[0]
            assert ended_span.status == "error"
        finally:
            set_global_tracer(None)

    async def test_traced_extracts_ctx_from_args(self) -> None:
        store = AsyncMock()
        tracer = TracerImpl(store)
        set_global_tracer(tracer)
        try:
            ctx = _ctx()

            @traced
            async def my_func(ctx: TenantContext, x: int) -> int:
                return x

            await my_func(ctx, 7)

            started_span = store.start.call_args.args[0]
            assert started_span.tenant_id == ctx.tenant_id
            assert started_span.agent_id == ctx.agent_id
        finally:
            set_global_tracer(None)

    async def test_global_tracer_get_and_set(self) -> None:
        assert get_global_tracer() is None
        tracer = TracerImpl(AsyncMock())
        set_global_tracer(tracer)
        assert get_global_tracer() is tracer
        set_global_tracer(None)
        assert get_global_tracer() is None
