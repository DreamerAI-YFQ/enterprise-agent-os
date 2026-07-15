"""Tests for TraceReplayerImpl — historical trace replay + LLM judge adoption."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from eaos.evolution.replay import TraceReplayerImpl


class _MockReplayDb:
    """Mock DB with fetch (spans) + fetch_one (strategy/training_run)."""

    def __init__(self) -> None:
        self._fetch_result: list[dict[str, Any]] = []
        self._fetch_one_result: dict[str, Any] | None = None

    async def execute(self, sql: str, *params: Any) -> None:
        pass

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        return self._fetch_result

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        return self._fetch_one_result


class _MockReplayLLM:
    """Mock LLM — returns canned responses for prompts."""

    def __init__(self, new_response: str = "new answer", judge_yes: bool = True) -> None:
        self._new_response = new_response
        self._judge_yes = judge_yes
        self.call_count = 0

    async def chat(self, prompt: str, model: str) -> str:
        self.call_count += 1
        if "判断" in prompt or "judge" in prompt.lower():
            return "yes" if self._judge_yes else "no"
        return self._new_response


def _make_span(
    *,
    name: str = "查询销售数据",
    output: str = "销售额为100万",
    duration_ms: int = 500,
) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "name": name,
        "attributes": json.dumps({"output": output, "duration_ms": duration_ms}),
    }


def _make_strategy_row(
    *,
    tenant_id: UUID | None = None,
    base_model: str = "qwen3-omni-flash",
    model_artifact_path: str | None = None,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id or uuid4(),
        "base_model": base_model,
        "model_artifact_path": model_artifact_path,
    }


class TestReplay:
    async def test_replay_returns_baseline_and_shadow_metrics(self) -> None:
        db = _MockReplayDb()
        db._fetch_result = [_make_span()]
        db._fetch_one_result = _make_strategy_row(
            model_artifact_path="qwen3-omni-flash",
        )
        llm = _MockReplayLLM(new_response="销售额为120万", judge_yes=True)
        replayer = TraceReplayerImpl(db=db, llm=llm, judge_model="qwen3-omni-flash")

        baseline, shadow = await replayer.replay(
            strategy_id=uuid4(),
            tenant_id=uuid4(),
            baseline_model="qwen3-omni-flash",
            new_model="qwen3-omni-flash",
            sample_size=1,
        )

        assert baseline["adoption_rate"] == 1.0
        assert "avg_latency_ms" in baseline
        assert "cost_usd" in baseline
        assert "error_rate" in baseline
        assert shadow["adoption_rate"] == 1.0  # judge said yes
        assert shadow["error_rate"] == 0.0
        assert shadow["avg_latency_ms"] >= 0.0
        assert shadow["cost_usd"] >= 0.0

    async def test_judge_no_counts_as_not_adopted(self) -> None:
        db = _MockReplayDb()
        db._fetch_result = [_make_span()]
        db._fetch_one_result = _make_strategy_row()
        llm = _MockReplayLLM(judge_yes=False)
        replayer = TraceReplayerImpl(db=db, llm=llm, judge_model="qwen3-omni-flash")

        _, shadow = await replayer.replay(
            strategy_id=uuid4(),
            tenant_id=uuid4(),
            baseline_model="qwen3-omni-flash",
            new_model="qwen3-omni-flash",
            sample_size=1,
        )

        assert shadow["adoption_rate"] == 0.0

    async def test_llm_error_counts_as_error(self) -> None:
        db = _MockReplayDb()
        db._fetch_result = [_make_span()]
        db._fetch_one_result = _make_strategy_row()

        class _ErrorLLM:
            async def chat(self, prompt: str, model: str) -> str:
                raise RuntimeError("API timeout")

        replayer = TraceReplayerImpl(db=db, llm=_ErrorLLM(), judge_model="m")
        _, shadow = await replayer.replay(
            strategy_id=uuid4(),
            tenant_id=uuid4(),
            baseline_model="qwen3-omni-flash",
            new_model="qwen3-omni-flash",
            sample_size=1,
        )

        assert shadow["error_rate"] == 1.0
        assert shadow["adoption_rate"] == 0.0

    async def test_empty_spans_returns_zero_metrics(self) -> None:
        db = _MockReplayDb()
        db._fetch_result = []  # no historical spans
        db._fetch_one_result = _make_strategy_row()
        llm = _MockReplayLLM()
        replayer = TraceReplayerImpl(db=db, llm=llm, judge_model="m")

        baseline, shadow = await replayer.replay(
            strategy_id=uuid4(),
            tenant_id=uuid4(),
            baseline_model="qwen3-omni-flash",
            new_model="qwen3-omni-flash",
            sample_size=10,
        )

        assert baseline["adoption_rate"] == 1.0
        assert shadow["adoption_rate"] == 0.0
        assert shadow["error_rate"] == 1.0  # no spans = all failed

    async def test_missing_output_skips_span(self) -> None:
        db = _MockReplayDb()
        db._fetch_result = [
            {"id": str(uuid4()), "name": "q", "attributes": json.dumps({})},  # no output
            _make_span(),
        ]
        db._fetch_one_result = _make_strategy_row()
        llm = _MockReplayLLM(judge_yes=True)
        replayer = TraceReplayerImpl(db=db, llm=llm, judge_model="m")

        baseline, shadow = await replayer.replay(
            strategy_id=uuid4(),
            tenant_id=uuid4(),
            baseline_model="qwen3-omni-flash",
            new_model="qwen3-omni-flash",
            sample_size=10,
        )

        # only 1 valid span, judge yes → adoption 1.0
        assert shadow["adoption_rate"] == 1.0
