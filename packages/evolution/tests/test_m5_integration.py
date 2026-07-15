"""M5 integration tests — end-to-end evolution cycle validation.

Requires a live PostgreSQL with migrations applied and seed data loaded.
Set ``EAOS_RUN_INTEGRATION=1`` to run.

Covers: feedback collection from trace, dataset building, DPO training (mock
trl), guardrail safety/perf benchmarks, six-step governance pipeline, and
rollback governance.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from eaos.evolution.pipeline import EvolutionPipelineImpl
    from eaos.evolution.trainer import DPOTrainerImpl
    from eaos.infra.db.base import DbClient

pytestmark = pytest.mark.integration

TEST_TENANT = UUID("00000000-0000-0000-0000-000000000555")
USER = UUID("00000000-0000-0000-0000-000000000201")
AGENT = UUID("00000000-0000-0000-0000-000000000301")
SAFETY_CASES_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "safety_cases_test.yaml"
)


# -- Mock helpers --------------------------------------------------------------


def _mock_train_fn(**kwargs: Any) -> tuple[str, dict[str, Any]]:
    """Sync mock for trl DPOTrainer — returns immediately without loading torch."""
    return ("/tmp/mock-model", {"loss": 0.5, "accuracy": 0.9})


class _MockSafetyLLM:
    """LLM that refuses unsafe prompts when safe=True, answers all when False."""

    _UNSAFE_KEYWORDS = ("password", "hack", "delete")

    def __init__(self, *, safe: bool = True) -> None:
        self._safe = safe

    async def chat(self, prompt: str, model: str) -> str:
        if self._safe:
            lower = prompt.lower()
            if any(kw in lower for kw in self._UNSAFE_KEYWORDS):
                return "Sorry, I cannot help with that."
        return "Here is the information you requested."


async def _mock_model_resolver(strategy_id: UUID) -> str:
    return "mock-strategy-model"


def _make_metrics_resolver(
    metrics: dict[str, Any],
) -> Callable[[UUID], Awaitable[dict[str, Any]]]:
    async def resolver(strategy_id: UUID) -> dict[str, Any]:
        return metrics

    return resolver


# -- Seed helpers --------------------------------------------------------------


async def _seed_span(
    db: DbClient,
    *,
    span_id: UUID,
    name: str,
    output: str,
    session_id: UUID,
    trace_id: UUID | None = None,
    start_time: datetime | None = None,
    attributes_extra: dict[str, Any] | None = None,
) -> None:
    attrs: dict[str, Any] = {"output": output}
    if attributes_extra:
        attrs.update(attributes_extra)
    await db.execute(
        """INSERT INTO trace.spans
           (id, tenant_id, trace_id, agent_id, session_id, granularity,
            name, start_time, status, attributes, user_id)
           VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10)""",
        span_id,
        TEST_TENANT,
        trace_id or uuid4(),
        AGENT,
        session_id,
        "task",
        name,
        start_time or datetime.utcnow(),
        "ok",
        json.dumps(attrs),
        USER,
    )


async def _seed_feedback_signal(
    db: DbClient,
    *,
    span_id: UUID,
    trace_id: UUID,
    signal_type: str,
    signal_value: str,
    strength: float = 0.8,
) -> None:
    await db.execute(
        """INSERT INTO evolution.feedback_signals
           (id, tenant_id, trace_id, span_id, user_id, agent_id,
            signal_type, signal_value, strength)
           VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8)""",
        uuid4(),
        TEST_TENANT,
        trace_id,
        span_id,
        USER,
        AGENT,
        signal_type,
        signal_value,
        strength,
    )


async def _seed_strategy(
    db: DbClient,
    *,
    strategy_id: UUID,
    run_id: UUID,
    stage: str = "guardrail",
    stage_status: str = "running",
    stage_detail: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        """INSERT INTO harness.evolution_strategies
           (id, tenant_id, training_run_id, stage, stage_status, stage_detail)
           VALUES (:p0, :p1, :p2, :p3, :p4, :p5)""",
        strategy_id,
        TEST_TENANT,
        run_id,
        stage,
        stage_status,
        json.dumps(stage_detail or {}),
    )


async def _seed_dataset(db: DbClient, dataset_id: UUID, name: str = "test-ds") -> None:
    await db.execute(
        "INSERT INTO evolution.datasets(id, tenant_id, name) "
        "VALUES (:p0, :p1, :p2)",
        dataset_id,
        TEST_TENANT,
        name,
    )


async def _seed_completed_training_run(
    db: DbClient,
    *,
    run_id: UUID,
    dataset_id: UUID,
    base_model: str = "mock-base",
    metrics: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        """INSERT INTO evolution.training_runs
           (id, tenant_id, dataset_id, base_model, method, status, metrics,
            started_at, completed_at)
           VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, NOW(), NOW())""",
        run_id,
        TEST_TENANT,
        dataset_id,
        base_model,
        "dpo",
        "completed",
        json.dumps(metrics or {"loss": 0.5, "accuracy": 0.9}),
    )


async def _seed_pipeline_test_data(db: DbClient) -> None:
    """Seed spans producing positive+negative signals for 'what is eaos'.

    Session 1: ADOPTED (positive) — next span has a different name.
    Session 2: REASKED (negative) — next span has a similar name.
    Both spans share the same prompt so the dataset builder can pair them.
    """
    session1 = uuid4()
    session2 = uuid4()
    trace1 = uuid4()
    trace2 = uuid4()
    now = datetime.utcnow()

    await _seed_span(
        db,
        span_id=uuid4(),
        name="what is eaos",
        output="EAOS is an agent OS",
        session_id=session1,
        trace_id=trace1,
        start_time=now,
    )
    await _seed_span(
        db,
        span_id=uuid4(),
        name="thanks",
        output="welcome",
        session_id=session1,
        trace_id=trace1,
        start_time=now + timedelta(seconds=1),
    )

    await _seed_span(
        db,
        span_id=uuid4(),
        name="what is eaos",
        output="EAOS is a platform",
        session_id=session2,
        trace_id=trace2,
        start_time=now + timedelta(seconds=2),
    )
    await _seed_span(
        db,
        span_id=uuid4(),
        name="what is eaos",
        output="EAOS is an enterprise platform",
        session_id=session2,
        trace_id=trace2,
        start_time=now + timedelta(seconds=3),
    )


async def _cleanup(db: DbClient) -> None:
    """Delete all M5 test data for the test tenant."""
    await db.execute(
        "DELETE FROM evolution.preference_pairs WHERE tenant_id = :p0", TEST_TENANT
    )
    await db.execute(
        "DELETE FROM evolution.training_runs WHERE tenant_id = :p0", TEST_TENANT
    )
    await db.execute(
        "DELETE FROM evolution.datasets WHERE tenant_id = :p0", TEST_TENANT
    )
    await db.execute(
        "DELETE FROM evolution.feedback_signals WHERE tenant_id = :p0", TEST_TENANT
    )
    await db.execute(
        "DELETE FROM harness.evolution_strategies WHERE tenant_id = :p0", TEST_TENANT
    )
    await db.execute(
        "DELETE FROM trace.spans WHERE tenant_id = :p0", TEST_TENANT
    )


@pytest.fixture(autouse=True)
async def _clean_test_tenant(db: DbClient) -> AsyncGenerator[None, None]:
    await _cleanup(db)
    yield
    await _cleanup(db)


# -- Component factory ---------------------------------------------------------


def _make_pipeline(
    db: DbClient,
    *,
    llm_safe: bool = True,
    shadow_passed: bool = True,
    approval_gate: Any | None = None,
    poll_interval: float = 0.05,
) -> tuple[EvolutionPipelineImpl, DPOTrainerImpl]:
    """Construct EvolutionPipelineImpl with real DB-backed components.

    Returns (pipeline, trainer) so callers can await both background task sets.
    Shadow is mocked since the real ShadowTrafficManagerImpl.start() overwrites
    stage_detail (design gap in Phase 5 simplified replay approach).
    """
    from eaos.evolution.dataset import PreferenceDatasetBuilderImpl
    from eaos.evolution.feedback import FeedbackCollectorImpl
    from eaos.evolution.guardrail import GuardrailCheckerImpl, GuardrailResult
    from eaos.evolution.pipeline import EvolutionPipelineImpl
    from eaos.evolution.trainer import DPOTrainerImpl

    feedback = FeedbackCollectorImpl(db)
    builder = PreferenceDatasetBuilderImpl(db)
    trainer = DPOTrainerImpl(db, builder, train_fn=_mock_train_fn)

    llm = _MockSafetyLLM(safe=llm_safe)
    guardrail = GuardrailCheckerImpl(
        llm=llm,
        model_resolver=_mock_model_resolver,
        metrics_resolver=_make_metrics_resolver(
            {"adoption_rate": 0.85, "avg_latency_ms": 950, "cost_usd": 0.48}
        ),
        safety_cases_path=SAFETY_CASES_PATH,
    )

    shadow: Any = AsyncMock()
    shadow.evaluate.return_value = GuardrailResult(
        passed=shadow_passed, reason="ok" if shadow_passed else "degraded"
    )

    pipeline = EvolutionPipelineImpl(
        db=db,
        feedback_collector=feedback,
        dataset_builder=builder,
        trainer=trainer,
        guardrail=guardrail,
        shadow=shadow,
        approval_gate=approval_gate,
        poll_interval=poll_interval,
    )
    return pipeline, trainer


# -- Feedback Collection Tests -------------------------------------------------


class TestFeedbackCollection:
    async def test_feedback_collected_from_trace(self, db: DbClient) -> None:
        from eaos.evolution.feedback import FeedbackCollectorImpl

        session_id = uuid4()
        trace_id = uuid4()
        now = datetime.utcnow()
        await _seed_span(
            db,
            span_id=uuid4(),
            name="what is eaos",
            output="EAOS is an agent OS",
            session_id=session_id,
            trace_id=trace_id,
            start_time=now,
        )
        await _seed_span(
            db,
            span_id=uuid4(),
            name="thanks",
            output="welcome",
            session_id=session_id,
            trace_id=trace_id,
            start_time=now + timedelta(seconds=1),
        )

        collector = FeedbackCollectorImpl(db)
        signals = await collector.collect_from_session(session_id)

        assert len(signals) == 2
        assert signals[0].signal_value == "positive"
        assert signals[1].signal_value == "negative"

    async def test_reasked_signal_inferred(self, db: DbClient) -> None:
        from eaos.evolution.feedback import FeedbackCollectorImpl, SignalType

        session_id = uuid4()
        trace_id = uuid4()
        now = datetime.utcnow()
        await _seed_span(
            db,
            span_id=uuid4(),
            name="what is eaos",
            output="EAOS is a platform",
            session_id=session_id,
            trace_id=trace_id,
            start_time=now,
        )
        await _seed_span(
            db,
            span_id=uuid4(),
            name="what is eaos",
            output="EAOS is an enterprise platform",
            session_id=session_id,
            trace_id=trace_id,
            start_time=now + timedelta(seconds=1),
        )

        collector = FeedbackCollectorImpl(db)
        signals = await collector.collect_from_session(session_id)

        assert len(signals) == 2
        assert signals[0].signal_type == SignalType.REASKED
        assert signals[0].signal_value == "negative"


# -- Dataset Building Tests ----------------------------------------------------


class TestDatasetBuilding:
    async def test_dataset_built_from_signals(self, db: DbClient) -> None:
        from eaos.evolution.dataset import PreferenceDatasetBuilderImpl

        span_a = uuid4()
        span_c = uuid4()
        trace_a = uuid4()
        trace_c = uuid4()
        await _seed_span(
            db,
            span_id=span_a,
            name="what is eaos",
            output="EAOS is an agent OS",
            session_id=uuid4(),
            trace_id=trace_a,
        )
        await _seed_span(
            db,
            span_id=span_c,
            name="what is eaos",
            output="EAOS is a platform",
            session_id=uuid4(),
            trace_id=trace_c,
        )
        await _seed_feedback_signal(
            db,
            span_id=span_a,
            trace_id=trace_a,
            signal_type="adopted",
            signal_value="positive",
        )
        await _seed_feedback_signal(
            db,
            span_id=span_c,
            trace_id=trace_c,
            signal_type="reasked",
            signal_value="negative",
        )

        builder = PreferenceDatasetBuilderImpl(db)
        dataset_id = await builder.build(TEST_TENANT)

        pairs = await builder.get_pairs(dataset_id)
        assert len(pairs) == 1
        pair = pairs[0]
        assert pair.prompt == "what is eaos"
        assert pair.chosen == "EAOS is an agent OS"
        assert pair.rejected == "EAOS is a platform"


# -- DPO Training Tests --------------------------------------------------------


class TestTraining:
    async def test_training_completed(self, db: DbClient) -> None:
        from eaos.evolution.dataset import PreferenceDatasetBuilderImpl
        from eaos.evolution.trainer import DPOTrainerImpl, TrainingStatus

        span_a = uuid4()
        span_c = uuid4()
        trace_a = uuid4()
        trace_c = uuid4()
        await _seed_span(
            db,
            span_id=span_a,
            name="what is eaos",
            output="EAOS is an agent OS",
            session_id=uuid4(),
            trace_id=trace_a,
        )
        await _seed_span(
            db,
            span_id=span_c,
            name="what is eaos",
            output="EAOS is a platform",
            session_id=uuid4(),
            trace_id=trace_c,
        )
        await _seed_feedback_signal(
            db,
            span_id=span_a,
            trace_id=trace_a,
            signal_type="adopted",
            signal_value="positive",
        )
        await _seed_feedback_signal(
            db,
            span_id=span_c,
            trace_id=trace_c,
            signal_type="reasked",
            signal_value="negative",
        )

        builder = PreferenceDatasetBuilderImpl(db)
        dataset_id = await builder.build(TEST_TENANT)

        trainer = DPOTrainerImpl(db, builder, train_fn=_mock_train_fn)
        run = await trainer.train(dataset_id, "mock-base", TEST_TENANT)

        await asyncio.gather(*trainer._tasks, return_exceptions=True)

        updated = await trainer.get_run(run.id)
        assert updated.status == TrainingStatus.COMPLETED
        assert updated.metrics.get("loss") == 0.5
        assert updated.model_artifact_path == "/tmp/mock-model"


# -- Guardrail Tests -----------------------------------------------------------


class TestGuardrail:
    async def test_safety_benchmark_blocks_pii(self, db: DbClient) -> None:
        from eaos.evolution.guardrail import GuardrailCheckerImpl

        strategy_id = uuid4()
        guardrail = GuardrailCheckerImpl(
            llm=_MockSafetyLLM(safe=False),
            model_resolver=_mock_model_resolver,
            safety_cases_path=SAFETY_CASES_PATH,
        )

        result = await guardrail.safety_benchmark(strategy_id)
        assert not result.passed
        assert result.details is not None
        assert result.details["total"] == 3
        assert result.details["passed"] == 1

    async def test_perf_compare_passes(self, db: DbClient) -> None:
        from eaos.evolution.guardrail import GuardrailCheckerImpl

        strategy_id = uuid4()
        baseline = {"adoption_rate": 0.8, "avg_latency_ms": 1000, "cost_usd": 0.5}
        new_metrics = {"adoption_rate": 0.85, "avg_latency_ms": 950, "cost_usd": 0.48}
        guardrail = GuardrailCheckerImpl(
            llm=_MockSafetyLLM(safe=True),
            model_resolver=_mock_model_resolver,
            metrics_resolver=_make_metrics_resolver(new_metrics),
            safety_cases_path=SAFETY_CASES_PATH,
        )

        result = await guardrail.perf_compare(strategy_id, baseline)
        assert result.passed


# -- Pipeline Tests ------------------------------------------------------------


class TestPipeline:
    async def test_full_pipeline_passes(self, db: DbClient) -> None:
        await _seed_pipeline_test_data(db)

        approval_gate = AsyncMock()
        approval_gate.request_approval.return_value = uuid4()

        pipeline, trainer = _make_pipeline(
            db,
            llm_safe=True,
            shadow_passed=True,
            approval_gate=approval_gate,
            poll_interval=0.05,
        )

        await pipeline.run(TEST_TENANT, "mock-base")

        await asyncio.gather(
            *trainer._tasks, *pipeline._tasks, return_exceptions=True
        )

        status = await pipeline.get_status(TEST_TENANT)
        assert status["stage"] == "approval"
        assert status["stage_status"] == "running"
        approval_gate.request_approval.assert_awaited_once()

    async def test_pipeline_blocks_on_safety_failure(self, db: DbClient) -> None:
        await _seed_pipeline_test_data(db)

        pipeline, trainer = _make_pipeline(
            db,
            llm_safe=False,
            shadow_passed=True,
            poll_interval=0.05,
        )

        await pipeline.run(TEST_TENANT, "mock-base")

        await asyncio.gather(
            *trainer._tasks, *pipeline._tasks, return_exceptions=True
        )

        status = await pipeline.get_status(TEST_TENANT)
        assert status["stage"] == "guardrail"
        assert status["stage_status"] == "blocked"

    async def test_pipeline_blocks_on_shadow_failure(self, db: DbClient) -> None:
        await _seed_pipeline_test_data(db)

        pipeline, trainer = _make_pipeline(
            db,
            llm_safe=True,
            shadow_passed=False,
            poll_interval=0.05,
        )

        await pipeline.run(TEST_TENANT, "mock-base")

        await asyncio.gather(
            *trainer._tasks, *pipeline._tasks, return_exceptions=True
        )

        status = await pipeline.get_status(TEST_TENANT)
        assert status["stage"] == "shadow"
        assert status["stage_status"] == "blocked"


# -- Governance Tests ----------------------------------------------------------


class TestGovernance:
    async def test_advance_canary_after_approval(self, db: DbClient) -> None:
        strategy_id = uuid4()
        run_id = uuid4()
        dataset_id = uuid4()
        await _seed_dataset(db, dataset_id)
        await _seed_completed_training_run(db, run_id=run_id, dataset_id=dataset_id)
        await _seed_strategy(
            db,
            strategy_id=strategy_id,
            run_id=run_id,
            stage="approval",
            stage_status="running",
        )

        pipeline, _trainer = _make_pipeline(db)
        await pipeline.advance_canary(strategy_id)

        row = await db.fetch_one(
            "SELECT stage, stage_status FROM harness.evolution_strategies "
            "WHERE id = :p0",
            strategy_id,
        )
        assert row is not None
        assert row["stage"] == "canary"
        assert row["stage_status"] == "running"

    async def test_rollback_restores_baseline(self, db: DbClient) -> None:
        strategy_id = uuid4()
        run_id = uuid4()
        dataset_id = uuid4()
        await _seed_dataset(db, dataset_id)
        await _seed_completed_training_run(db, run_id=run_id, dataset_id=dataset_id)
        await _seed_strategy(
            db,
            strategy_id=strategy_id,
            run_id=run_id,
            stage="canary",
            stage_status="running",
        )

        pipeline, _trainer = _make_pipeline(db)
        await pipeline.rollback(strategy_id)

        row = await db.fetch_one(
            "SELECT stage, stage_status FROM harness.evolution_strategies "
            "WHERE id = :p0",
            strategy_id,
        )
        assert row is not None
        assert row["stage"] == "rolled_back"
        assert row["stage_status"] == "completed"
