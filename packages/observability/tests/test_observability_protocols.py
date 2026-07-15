"""Verify observability Protocols match Phase 0 contract."""

from __future__ import annotations

import dataclasses

from eaos.observability.decorators import traced
from eaos.observability.query import DateRange, Overview, TraceQuery
from eaos.observability.span import Granularity, Span, SpanEvent
from eaos.observability.store import TraceStore
from eaos.observability.tracer import SpanHandle, Tracer


class TestSpan:
    def test_granularity_values(self) -> None:
        assert Granularity.CALL.value == "call"
        assert Granularity.TOOL.value == "tool"
        assert Granularity.TASK.value == "task"
        assert Granularity.SESSION.value == "session"

    def test_spanevent_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(SpanEvent)}
        assert {"name", "timestamp", "attributes"} <= fields

    def test_span_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(Span)}
        assert {
            "id",
            "tenant_id",
            "trace_id",
            "parent_span_id",
            "agent_id",
            "session_id",
            "granularity",
            "name",
            "start_time",
            "end_time",
            "duration_ms",
            "status",
            "attributes",
            "events",
            "cost_tokens",
            "cost_usd",
            "user_id",
        } <= fields


class TestTracer:
    def test_spanhandle_methods(self) -> None:
        for method in ("set_attribute", "add_event", "set_status"):
            assert hasattr(SpanHandle, method)

    def test_tracer_methods(self) -> None:
        assert hasattr(Tracer, "span")


class TestStore:
    def test_methods(self) -> None:
        for method in ("start", "end", "get", "query", "get_trace"):
            assert hasattr(TraceStore, method)


class TestQuery:
    def test_overview_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(Overview)}
        assert {
            "tenant_id",
            "total_agents",
            "active_users_today",
            "total_tokens_today",
            "top_skills",
        } <= fields

    def test_daterange_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(DateRange)}
        assert {"start", "end"} <= fields

    def test_query_methods(self) -> None:
        for method in ("overview", "by_department", "by_agent", "by_skill", "trace_detail"):
            assert hasattr(TraceQuery, method)


class TestTracedDecorator:
    def test_decorator_exists(self) -> None:
        assert callable(traced)

    def test_decorator_preserves_function(self) -> None:
        @traced
        async def my_func(x: int) -> int:
            return x * 2

        assert my_func.__name__ == "my_func"

    def test_decorator_accepts_name_arg(self) -> None:
        @traced(name="custom_name")
        async def my_func(x: int) -> int:
            return x * 2

        assert my_func.__name__ == "my_func"
