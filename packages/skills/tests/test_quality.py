"""Tests for PgSkillQualityMonitor — record, metrics, auto-deprecate."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from eaos.skills.quality import PgSkillQualityMonitor


class TestRecord:
    async def test_record_success_upserts(self) -> None:
        db = AsyncMock()
        registry = AsyncMock()
        monitor = PgSkillQualityMonitor(db, registry)

        await monitor.record(uuid4(), uuid4(), success=True, latency_ms=120)

        sql = db.execute.call_args.args[0]
        assert "INSERT INTO harness.quality_metrics" in sql
        assert "ON CONFLICT" in sql
        positional = db.execute.call_args.args[1:]
        # (tenant_id, skill_id, success_inc, failure_inc, latency)
        assert positional[2] == 1  # success_inc
        assert positional[3] == 0  # failure_inc

    async def test_record_failure_upserts(self) -> None:
        db = AsyncMock()
        registry = AsyncMock()
        monitor = PgSkillQualityMonitor(db, registry)

        await monitor.record(uuid4(), uuid4(), success=False, latency_ms=200)

        positional = db.execute.call_args.args[1:]
        assert positional[2] == 0  # success_inc
        assert positional[3] == 1  # failure_inc


class TestGetMetrics:
    async def test_metrics_zero_when_no_rows(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = [
            {"total_calls": 0, "total_success": 0, "total_failure": 0, "avg_latency": None}
        ]
        registry = AsyncMock()
        monitor = PgSkillQualityMonitor(db, registry)

        result = await monitor.get_metrics(uuid4(), uuid4())

        assert result.call_count == 0
        assert result.failure_rate == 0.0
        assert result.avg_latency_ms is None

    async def test_metrics_aggregates(self) -> None:
        skill_id = uuid4()
        db = AsyncMock()
        db.fetch.return_value = [
            {
                "total_calls": 10,
                "total_success": 7,
                "total_failure": 3,
                "avg_latency": 150,
            }
        ]
        registry = AsyncMock()
        monitor = PgSkillQualityMonitor(db, registry)

        result = await monitor.get_metrics(skill_id, uuid4(), window_hours=48)

        assert result.call_count == 10
        assert result.success_count == 7
        assert result.failure_count == 3
        assert result.failure_rate == pytest.approx(0.3)
        assert result.avg_latency_ms == 150
        assert result.skill_id == skill_id

    async def test_metrics_empty_result_set(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = []
        registry = AsyncMock()
        monitor = PgSkillQualityMonitor(db, registry)

        result = await monitor.get_metrics(uuid4(), uuid4())
        assert result.call_count == 0


class TestCheckAutoDeprecate:
    async def test_deprecates_when_failure_rate_exceeds_threshold(self) -> None:
        skill_id = uuid4()
        tenant_id = uuid4()
        db = AsyncMock()
        db.fetch.return_value = [
            {
                "total_calls": 10,
                "total_success": 5,
                "total_failure": 5,
                "avg_latency": 100,
            }
        ]
        registry = AsyncMock()
        monitor = PgSkillQualityMonitor(db, registry)

        result = await monitor.check_auto_deprecate(skill_id, tenant_id)

        assert result is True
        registry.deprecate.assert_awaited_once()
        deprecate_args = registry.deprecate.call_args.args
        assert deprecate_args[0] == skill_id
        assert deprecate_args[1] == tenant_id

    async def test_no_deprecate_when_failure_rate_low(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = [
            {
                "total_calls": 100,
                "total_success": 95,
                "total_failure": 5,
                "avg_latency": 100,
            }
        ]
        registry = AsyncMock()
        monitor = PgSkillQualityMonitor(db, registry)

        result = await monitor.check_auto_deprecate(uuid4(), uuid4())

        assert result is False
        registry.deprecate.assert_not_awaited()

    async def test_no_deprecate_when_no_calls(self) -> None:
        db = AsyncMock()
        db.fetch.return_value = [
            {"total_calls": 0, "total_success": 0, "total_failure": 0, "avg_latency": None}
        ]
        registry = AsyncMock()
        monitor = PgSkillQualityMonitor(db, registry)

        result = await monitor.check_auto_deprecate(UUID(int=0), UUID(int=0))

        assert result is False
        registry.deprecate.assert_not_awaited()
