"""Tests for ShadowTrafficManagerImpl — shadow traffic state and metrics evaluation."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from eaos.evolution.shadow import ShadowTrafficManagerImpl


class _MockDb:
    def __init__(self) -> None:
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self._fetch_one_result: dict[str, Any] | None = None

    async def execute(self, sql: str, *params: Any) -> None:
        self.executes.append((sql, params))

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        return self._fetch_one_result


def _detail_row(
    *,
    baseline: dict[str, Any] | None = None,
    shadow: dict[str, Any] | None = None,
    traffic_pct: int = 10,
    duration_hours: int = 24,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "traffic_pct": traffic_pct,
        "duration_hours": duration_hours,
    }
    if baseline is not None:
        detail["baseline_metrics"] = baseline
    if shadow is not None:
        detail["shadow_results"] = shadow
    return {"stage": "shadow", "stage_status": "running", "stage_detail": json.dumps(detail)}


class TestStart:
    async def test_start_updates_strategy(self) -> None:
        db = _MockDb()
        mgr = ShadowTrafficManagerImpl(db)
        sid = uuid4()
        await mgr.start(sid, traffic_pct=15, duration_hours=48)
        sql, params = db.executes[-1]
        assert "UPDATE harness.evolution_strategies" in sql
        assert params[0] == "shadow"
        assert params[1] == "running"
        detail = json.loads(params[2])
        assert detail["traffic_pct"] == 15
        assert detail["duration_hours"] == 48
        assert "started_at" in detail
        assert params[3] == sid


class TestEvaluate:
    async def test_all_metrics_pass(self) -> None:
        db = _MockDb()
        db._fetch_one_result = _detail_row(
            baseline={
                "adoption_rate": 0.90,
                "error_rate": 0.05,
                "avg_latency_ms": 100.0,
                "cost_usd": 0.020,
            },
            shadow={
                "adoption_rate": 0.85,
                "error_rate": 0.055,
                "avg_latency_ms": 115.0,
                "cost_usd": 0.025,
            },
        )
        mgr = ShadowTrafficManagerImpl(db)
        result = await mgr.evaluate(uuid4())
        assert result.passed is True
        assert result.details is not None
        metrics = result.details["metrics"]
        assert metrics["adoption_rate"]["passed"] is True
        assert metrics["error_rate"]["passed"] is True
        assert metrics["avg_latency_ms"]["passed"] is True
        assert metrics["cost_usd"]["passed"] is True

    async def test_adoption_fails(self) -> None:
        db = _MockDb()
        db._fetch_one_result = _detail_row(
            baseline={"adoption_rate": 0.95, "error_rate": 0.05,
                      "avg_latency_ms": 100.0, "cost_usd": 0.02},
            shadow={"adoption_rate": 0.80, "error_rate": 0.05,
                    "avg_latency_ms": 100.0, "cost_usd": 0.02},
        )
        mgr = ShadowTrafficManagerImpl(db)
        result = await mgr.evaluate(uuid4())
        assert result.passed is False
        assert result.details is not None
        assert result.details["metrics"]["adoption_rate"]["passed"] is False

    async def test_error_rate_fails(self) -> None:
        db = _MockDb()
        db._fetch_one_result = _detail_row(
            baseline={"adoption_rate": 0.90, "error_rate": 0.05,
                      "avg_latency_ms": 100.0, "cost_usd": 0.02},
            shadow={"adoption_rate": 0.90, "error_rate": 0.10,
                    "avg_latency_ms": 100.0, "cost_usd": 0.02},
        )
        mgr = ShadowTrafficManagerImpl(db)
        result = await mgr.evaluate(uuid4())
        assert result.passed is False
        assert result.details is not None
        assert result.details["metrics"]["error_rate"]["passed"] is False

    async def test_missing_metrics(self) -> None:
        db = _MockDb()
        db._fetch_one_result = _detail_row(baseline=None, shadow=None)
        mgr = ShadowTrafficManagerImpl(db)
        result = await mgr.evaluate(uuid4())
        assert result.passed is False
        assert result.reason is not None
        assert "Missing" in result.reason

    async def test_strategy_not_found(self) -> None:
        db = _MockDb()
        db._fetch_one_result = None
        mgr = ShadowTrafficManagerImpl(db)
        with pytest.raises(KeyError, match="not found"):
            await mgr.evaluate(uuid4())


class TestStop:
    async def test_stop_updates_status(self) -> None:
        db = _MockDb()
        mgr = ShadowTrafficManagerImpl(db)
        sid = uuid4()
        await mgr.stop(sid)
        sql, params = db.executes[-1]
        assert "UPDATE harness.evolution_strategies" in sql
        assert params[0] == "completed"
        assert params[1] == sid


class TestGetStatus:
    async def test_returns_status(self) -> None:
        db = _MockDb()
        db._fetch_one_result = _detail_row(
            baseline={"adoption_rate": 0.9},
            shadow={"adoption_rate": 0.88},
        )
        mgr = ShadowTrafficManagerImpl(db)
        status = await mgr.get_status(uuid4())
        assert status["stage"] == "shadow"
        assert status["stage_status"] == "running"
        assert status["traffic_pct"] == 10
        assert status["baseline_metrics"]["adoption_rate"] == 0.9
        assert status["shadow_results"]["adoption_rate"] == 0.88

    async def test_not_found_returns_empty(self) -> None:
        db = _MockDb()
        db._fetch_one_result = None
        mgr = ShadowTrafficManagerImpl(db)
        status = await mgr.get_status(uuid4())
        assert status == {}


class _MockReplayer:
    """Mock TraceReplayer — returns canned metrics."""

    def __init__(
        self,
        baseline: dict[str, Any] | None = None,
        shadow: dict[str, Any] | None = None,
    ) -> None:
        self._baseline = baseline or {
            "adoption_rate": 1.0,
            "avg_latency_ms": 100.0,
            "cost_usd": 0.0,
            "error_rate": 0.0,
        }
        self._shadow = shadow or {
            "adoption_rate": 0.9,
            "avg_latency_ms": 110.0,
            "cost_usd": 0.01,
            "error_rate": 0.05,
        }
        self.called = False

    async def replay(
        self,
        strategy_id: Any,
        tenant_id: Any,
        baseline_model: str,
        new_model: str,
        sample_size: int = 50,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.called = True
        return self._baseline, self._shadow


class TestStartWithReplayer:
    async def test_start_calls_replayer_and_merges_metrics(self) -> None:
        db = _MockDb()
        # start() queries strategy row for tenant_id + model info
        db._fetch_one_result = {
            "tenant_id": uuid4(),
            "base_model": "qwen3-omni-flash",
            "model_artifact_path": "qwen3-omni-flash",
        }
        replayer = _MockReplayer()
        mgr = ShadowTrafficManagerImpl(db, replayer=replayer)
        sid = uuid4()
        await mgr.start(sid)

        assert replayer.called is True
        # last execute is the merge: UPDATE ... SET stage_detail = :p0 WHERE id = :p1
        sql, params = db.executes[-1]
        assert "UPDATE harness.evolution_strategies" in sql
        detail = json.loads(params[0])  # stage_detail is 1st param of merge
        assert "baseline_metrics" in detail
        assert "shadow_results" in detail

    async def test_start_without_replayer_preserves_old_behavior(self) -> None:
        """Backward compat: no replayer -> no metrics merge."""
        db = _MockDb()
        db._fetch_one_result = {
            "tenant_id": uuid4(),
            "base_model": "qwen3-omni-flash",
            "model_artifact_path": None,
        }
        mgr = ShadowTrafficManagerImpl(db)  # no replayer
        sid = uuid4()
        await mgr.start(sid)

        # only the stage update execute, no second merge execute
        sql, params = db.executes[-1]
        detail = json.loads(params[2])
        assert "baseline_metrics" not in detail  # no merge happened
