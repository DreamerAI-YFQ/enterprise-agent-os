"""Tests for EvolutionPipelineImpl — six-step governance pipeline orchestration."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.evolution.guardrail import GuardrailResult
from eaos.evolution.pipeline import EvolutionPipelineImpl
from eaos.evolution.trainer import TrainingRun, TrainingStatus


class _MockDb:
    def __init__(self) -> None:
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self._fetch_result: list[dict[str, Any]] = []
        self._fetch_one_result: dict[str, Any] | None = None

    async def execute(self, sql: str, *params: Any) -> None:
        self.executes.append((sql, params))

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        return self._fetch_result

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        return self._fetch_one_result


def _make_pipeline(
    *,
    training_status: TrainingStatus = TrainingStatus.COMPLETED,
    safety_passed: bool = True,
    perf_passed: bool = True,
    shadow_passed: bool = True,
    approval_gate: Any | None = None,
) -> tuple[EvolutionPipelineImpl, _MockDb]:
    db = _MockDb()
    feedback = AsyncMock()
    feedback.collect_from_session.return_value = []
    builder = AsyncMock()
    builder.build.return_value = uuid4()
    trainer = AsyncMock()
    trainer.train.return_value = TrainingRun(
        id=uuid4(), status=TrainingStatus.QUEUED
    )
    trainer.get_run.return_value = TrainingRun(status=training_status)
    guardrail = AsyncMock()
    guardrail.safety_benchmark.return_value = GuardrailResult(
        passed=safety_passed, reason="ok"
    )
    guardrail.perf_compare.return_value = GuardrailResult(
        passed=perf_passed, reason="ok"
    )
    shadow = AsyncMock()
    shadow.evaluate.return_value = GuardrailResult(
        passed=shadow_passed, reason="ok"
    )
    pipeline = EvolutionPipelineImpl(
        db=db,
        feedback_collector=feedback,
        dataset_builder=builder,
        trainer=trainer,
        guardrail=guardrail,
        shadow=shadow,
        approval_gate=approval_gate,
    )
    return pipeline, db


class TestRun:
    async def test_returns_queued_training_run(self) -> None:
        pipeline, db = _make_pipeline()
        run = await pipeline.run(uuid4(), "base-model")
        assert run.status == TrainingStatus.QUEUED
        assert len(db.executes) >= 3
        await asyncio.gather(*pipeline._tasks, return_exceptions=True)


class TestRunGovernance:
    async def test_all_pass_reaches_approval(self) -> None:
        pipeline, db = _make_pipeline()
        await pipeline._run_governance(uuid4(), uuid4(), uuid4())
        _, params = db.executes[-1]
        assert params[0] == "approval"

    async def test_safety_fail_blocks(self) -> None:
        pipeline, db = _make_pipeline(safety_passed=False)
        await pipeline._run_governance(uuid4(), uuid4(), uuid4())
        _, params = db.executes[-1]
        assert params[0] == "blocked"
        assert "safety" in params[1]

    async def test_perf_fail_blocks(self) -> None:
        pipeline, db = _make_pipeline(perf_passed=False)
        await pipeline._run_governance(uuid4(), uuid4(), uuid4())
        _, params = db.executes[-1]
        assert params[0] == "blocked"
        assert "perf" in params[1]

    async def test_shadow_fail_blocks(self) -> None:
        pipeline, db = _make_pipeline(shadow_passed=False)
        await pipeline._run_governance(uuid4(), uuid4(), uuid4())
        _, params = db.executes[-1]
        assert params[0] == "blocked"
        assert "shadow" in params[1]

    async def test_training_failed_blocks(self) -> None:
        pipeline, db = _make_pipeline(
            training_status=TrainingStatus.FAILED
        )
        await pipeline._run_governance(uuid4(), uuid4(), uuid4())
        _, params = db.executes[-1]
        assert params[0] == "blocked"
        assert "training" in params[1]

    async def test_approval_gate_called(self) -> None:
        gate = AsyncMock()
        gate.request_approval.return_value = uuid4()
        pipeline, db = _make_pipeline(approval_gate=gate)
        await pipeline._run_governance(uuid4(), uuid4(), uuid4())
        gate.request_approval.assert_awaited_once()
        _, params = db.executes[-1]
        assert params[0] == "approval"


class TestGetStatus:
    async def test_returns_strategy(self) -> None:
        pipeline, db = _make_pipeline()
        db._fetch_one_result = {
            "id": uuid4(),
            "training_run_id": uuid4(),
            "stage": "shadow",
            "stage_status": "running",
            "stage_detail": json.dumps({"traffic_pct": 10}),
            "created_at": "2026-01-01",
        }
        status = await pipeline.get_status(uuid4())
        assert status["stage"] == "shadow"
        assert status["stage_status"] == "running"
        assert status["detail"]["traffic_pct"] == 10

    async def test_returns_empty_when_no_strategy(self) -> None:
        pipeline, db = _make_pipeline()
        db._fetch_one_result = None
        status = await pipeline.get_status(uuid4())
        assert status == {}


class TestCollectFeedbackOnly:
    async def test_returns_zero_when_no_sessions(self) -> None:
        pipeline, _db = _make_pipeline()
        count = await pipeline.collect_feedback_only(uuid4(), since_days=7)
        assert count == 0

    async def test_returns_signal_count(self) -> None:
        pipeline, db = _make_pipeline()
        db._fetch_result = [{"session_id": uuid4()}]
        pipeline._feedback.collect_from_session.return_value = [  # type: ignore[attr-defined]
            object(),
            object(),
        ]
        count = await pipeline.collect_feedback_only(uuid4(), since_days=7)
        assert count == 2


class TestListStrategies:
    async def test_returns_formatted_list(self) -> None:
        pipeline, db = _make_pipeline()
        db._fetch_result = [
            {
                "id": uuid4(),
                "training_run_id": uuid4(),
                "stage": "shadow",
                "stage_status": "running",
                "stage_detail": json.dumps({"k": 1}),
                "created_at": "2026-01-01",
            }
        ]
        result = await pipeline.list_strategies(uuid4())
        assert len(result) == 1
        assert result[0]["stage"] == "shadow"
        assert result[0]["detail"]["k"] == 1

    async def test_returns_empty_when_none(self) -> None:
        pipeline, _db = _make_pipeline()
        result = await pipeline.list_strategies(uuid4())
        assert result == []


class TestGetStrategy:
    async def test_returns_strategy_by_id(self) -> None:
        pipeline, db = _make_pipeline()
        sid = uuid4()
        db._fetch_one_result = {
            "id": sid,
            "training_run_id": uuid4(),
            "stage": "approval",
            "stage_status": "running",
            "stage_detail": json.dumps({"reason": "ok"}),
            "created_at": "2026-01-01",
        }
        result = await pipeline.get_strategy(sid)
        assert result["id"] == sid
        assert result["stage"] == "approval"
        assert result["detail"]["reason"] == "ok"

    async def test_returns_empty_when_not_found(self) -> None:
        pipeline, db = _make_pipeline()
        db._fetch_one_result = None
        result = await pipeline.get_strategy(uuid4())
        assert result == {}


class TestAdvanceCanary:
    async def test_updates_stage_to_canary(self) -> None:
        pipeline, db = _make_pipeline()
        sid = uuid4()
        await pipeline.advance_canary(sid)
        _, params = db.executes[-1]
        assert params[0] == "canary"
        assert params[1] == "running"
        assert params[2] == sid


class TestRollback:
    async def test_updates_stage_to_rolled_back(self) -> None:
        pipeline, db = _make_pipeline()
        sid = uuid4()
        await pipeline.rollback(sid)
        sql, params = db.executes[-1]
        assert "UPDATE harness.evolution_strategies" in sql
        assert params[0] == "rolled_back"
        assert params[1] == "completed"
        assert params[2] == sid
