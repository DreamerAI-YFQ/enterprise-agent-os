"""Tests for FeedbackCollectorImpl — implicit signal inference from spans."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.evolution.feedback import (
    FeedbackCollectorImpl,
    FeedbackSignal,
    SignalType,
    _infer_signal,
    _similarity,
)


def _span_row(
    *,
    name: str = "query",
    attrs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "trace_id": uuid4(),
        "agent_id": uuid4(),
        "session_id": uuid4(),
        "user_id": uuid4(),
        "name": name,
        "attributes": attrs or {},
        "start_time": datetime(2026, 1, 1),
    }


class TestSignalInference:
    """Unit tests for the _infer_signal priority logic."""

    def test_explicit_thumbs_up(self) -> None:
        row = _span_row(attrs={"feedback": "thumbs_up"})
        signal = _infer_signal([row], 0)
        assert signal.signal_type == SignalType.EXPLICIT
        assert signal.signal_value == "positive"
        assert signal.strength == 1.0

    def test_explicit_thumbs_down(self) -> None:
        row = _span_row(attrs={"feedback": "thumbs_down"})
        signal = _infer_signal([row], 0)
        assert signal.signal_type == SignalType.EXPLICIT
        assert signal.signal_value == "negative"

    def test_modified_output(self) -> None:
        row = _span_row(attrs={"output_modified_ratio": 0.8})
        signal = _infer_signal([row], 0)
        assert signal.signal_type == SignalType.MODIFIED
        assert signal.signal_value == "negative"
        assert signal.strength == 0.7

    def test_modified_below_threshold_is_not_modified(self) -> None:
        row = _span_row(attrs={"output_modified_ratio": 0.5})
        following = _span_row(name="different question")
        signal = _infer_signal([row, following], 0)
        assert signal.signal_type == SignalType.ADOPTED

    def test_used_unchanged(self) -> None:
        row = _span_row(attrs={"output_copied": True})
        signal = _infer_signal([row], 0)
        assert signal.signal_type == SignalType.USED_UNCHANGED
        assert signal.signal_value == "positive"
        assert signal.strength == 0.8

    def test_abandoned_no_following(self) -> None:
        row = _span_row()
        signal = _infer_signal([row], 0)
        assert signal.signal_type == SignalType.ABANDONED
        assert signal.signal_value == "negative"
        assert signal.strength == 0.9

    def test_reasked_similar_following(self) -> None:
        row = _span_row(name="查询本周销售报表")
        following = _span_row(name="查询本周销售报表汇总")
        signal = _infer_signal([row, following], 0)
        assert signal.signal_type == SignalType.REASKED
        assert signal.signal_value == "negative"
        assert signal.strength == 0.6

    def test_adopted_different_following(self) -> None:
        row = _span_row(name="查询销售报表")
        following = _span_row(name="帮我发一封邮件")
        signal = _infer_signal([row, following], 0)
        assert signal.signal_type == SignalType.ADOPTED
        assert signal.signal_value == "positive"
        assert signal.strength == 0.8

    def test_explicit_takes_priority_over_others(self) -> None:
        row = _span_row(attrs={"feedback": "thumbs_up", "output_copied": True})
        signal = _infer_signal([row], 0)
        assert signal.signal_type == SignalType.EXPLICIT

    def test_attributes_as_json_string(self) -> None:
        """DB may return JSONB as a string; _parse_jsonb should handle it."""
        row = _span_row()
        row["attributes"] = json.dumps({"feedback": "thumbs_up"})
        signal = _infer_signal([row], 0)
        assert signal.signal_type == SignalType.EXPLICIT


class TestSimilarity:
    def test_identical_strings(self) -> None:
        assert _similarity("hello", "hello") == 1.0

    def test_case_insensitive(self) -> None:
        assert _similarity("Hello", "hello") == 1.0

    def test_disjoint_strings_low(self) -> None:
        assert _similarity("abc", "xyz") < 0.5


class TestFeedbackCollectorImpl:
    async def test_collect_from_session_infers_signals(self) -> None:
        rows = [
            _span_row(name="query sales"),
            _span_row(name="send email"),
        ]
        db = AsyncMock()
        db.fetch.return_value = rows

        collector = FeedbackCollectorImpl(db)
        signals = await collector.collect_from_session(uuid4())

        assert len(signals) == 2
        assert signals[0].signal_type == SignalType.ADOPTED
        assert signals[1].signal_type == SignalType.ABANDONED
        db.fetch.assert_awaited_once()
        sql = db.fetch.call_args.args[0]
        assert "session_id = :p0" in sql
        assert "granularity = 'task'" in sql

    async def test_collect_from_trace_infers_signals(self) -> None:
        rows = [_span_row(name="solo task")]
        db = AsyncMock()
        db.fetch.return_value = rows

        collector = FeedbackCollectorImpl(db)
        signals = await collector.collect_from_trace(uuid4())

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.ABANDONED
        sql = db.fetch.call_args.args[0]
        assert "trace_id = :p0" in sql

    async def test_batch_save_inserts_each_signal(self) -> None:
        db = AsyncMock()
        collector = FeedbackCollectorImpl(db)
        signals = [
            FeedbackSignal(
                tenant_id=uuid4(),
                trace_id=uuid4(),
                span_id=uuid4(),
                user_id=uuid4(),
                agent_id=uuid4(),
                signal_type=SignalType.ADOPTED,
                signal_value="positive",
                strength=0.8,
            ),
            FeedbackSignal(
                tenant_id=uuid4(),
                trace_id=uuid4(),
                span_id=uuid4(),
                user_id=uuid4(),
                agent_id=uuid4(),
                signal_type=SignalType.REASKED,
                signal_value="negative",
                strength=0.6,
            ),
        ]

        await collector.batch_save(signals)

        assert db.execute.await_count == 2
        sql = db.execute.call_args.args[0]
        assert "INSERT INTO evolution.feedback_signals" in sql
        first_call_params = db.execute.call_args_list[0].args[1:]
        assert first_call_params[6] == "adopted"  # signal_type.value
        assert first_call_params[7] == "positive"

    async def test_get_signals_with_filters(self) -> None:
        tenant = uuid4()
        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 31)
        db = AsyncMock()
        db.fetch.return_value = [
            {
                "id": uuid4(),
                "tenant_id": tenant,
                "trace_id": uuid4(),
                "span_id": uuid4(),
                "user_id": uuid4(),
                "agent_id": uuid4(),
                "signal_type": "adopted",
                "signal_value": "positive",
                "strength": 0.8,
                "captured_at": datetime(2026, 1, 15),
            }
        ]

        collector = FeedbackCollectorImpl(db)
        signals = await collector.get_signals(
            tenant, date_range=(start, end), signal_value="positive"
        )

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.ADOPTED
        sql, *_params = db.fetch.call_args.args
        assert "tenant_id = :p0" in sql
        assert "captured_at >= :p1" in sql
        assert "captured_at <= :p2" in sql
        assert "signal_value = :p3" in sql

    async def test_get_signals_without_filters(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = []

        collector = FeedbackCollectorImpl(db)
        signals = await collector.get_signals(uuid4())

        assert signals == []
        sql = db.fetch.call_args.args[0]
        assert "tenant_id = :p0" in sql
        assert "captured_at >=" not in sql
        assert "signal_value =" not in sql

    async def test_empty_session_returns_empty(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = []

        collector = FeedbackCollectorImpl(db)
        signals = await collector.collect_from_session(uuid4())

        assert signals == []
