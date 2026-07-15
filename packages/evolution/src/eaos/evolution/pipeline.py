"""EvolutionPipeline — end-to-end Act -> Observe -> Learn loop.

Orchestrates: feedback collection -> dataset building -> DPO training ->
guardrail checks -> shadow traffic -> governance approval -> canary -> full.

This is the top-level facade; callers (scheduled jobs or admin API) invoke
run() to trigger a full cycle.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from eaos.evolution.trainer import TrainingStatus

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.evolution.dataset import PreferenceDatasetBuilder
    from eaos.evolution.feedback import FeedbackCollector
    from eaos.evolution.guardrail import GuardrailChecker
    from eaos.evolution.shadow import ShadowTrafficManager
    from eaos.evolution.trainer import DPOTrainer, TrainingRun


class EvolutionPipeline(Protocol):
    """End-to-end evolution loop orchestrator.

    Act (skills execute) -> Observe (trace records) -> Learn (RL trains) ->
    Harness governance (six-step) -> back to Act (smarter).
    """

    async def run(
        self,
        tenant_id: UUID,
        base_model: str,
    ) -> TrainingRun:
        """Trigger a full evolution cycle.

        Steps:
        1. Collect feedback signals since last run
        2. Build preference dataset
        3. Start DPO training (async, returns queued run)
        4. (On training completion) submit to Harness evolution governance
        5. (Governance) six-step pipeline runs automatically through shadow
        6. (Approval) notifies admins for human approval
        7. (Canary -> Full) gradual rollout with auto-rollback

        Returns the TrainingRun (status=queued). Monitor via get_run().
        """
        ...

    async def get_status(
        self,
        tenant_id: UUID,
    ) -> dict[str, Any]:
        """Get current evolution pipeline status for a tenant.

        Returns: last_run, current_strategy_in_governance, metrics_trend.
        """
        ...

    async def collect_feedback_only(
        self,
        tenant_id: UUID,
        since_days: int = 7,
    ) -> int:
        """Run only feedback collection (without training). Returns signal count."""
        ...


class PipelineDb(Protocol):
    """Minimal DB subset for evolution pipeline state persistence."""

    async def execute(self, sql: str, *params: Any) -> None: ...

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]: ...

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...


class ApprovalGate(Protocol):
    """Human-in-the-loop approval gate (Phase 4 EvolutionGuard)."""

    async def request_approval(
        self,
        strategy_id: UUID,
        run_id: UUID,
        tenant_id: UUID,
        reason: str,
    ) -> UUID:
        """Request admin approval for strategy rollout. Returns approval id."""
        ...


class EvolutionPipelineImpl:
    """EvolutionPipeline backed by harness.evolution_strategies.

    run() executes steps 1-4 synchronously (feedback -> dataset -> training
    submit) and returns the TrainingRun (status=queued). Steps 5-8
    (guardrail -> shadow -> approval) run in a background asyncio task that
    polls training completion. Any failure blocks the strategy.
    """

    def __init__(
        self,
        db: PipelineDb,
        feedback_collector: FeedbackCollector,
        dataset_builder: PreferenceDatasetBuilder,
        trainer: DPOTrainer,
        guardrail: GuardrailChecker,
        shadow: ShadowTrafficManager,
        approval_gate: ApprovalGate | None = None,
        *,
        poll_interval: float = 60.0,
    ) -> None:
        self._db = db
        self._feedback = feedback_collector
        self._builder = dataset_builder
        self._trainer = trainer
        self._guardrail = guardrail
        self._shadow = shadow
        self._approval_gate = approval_gate
        self._poll_interval = poll_interval
        self._tasks: set[asyncio.Task[None]] = set()

    async def run(
        self,
        tenant_id: UUID,
        base_model: str,
    ) -> TrainingRun:
        strategy_id = uuid4()
        await self._db.execute(
            """INSERT INTO harness.evolution_strategies
               (id, tenant_id, training_run_id, stage, stage_status, stage_detail)
               VALUES (:p0, :p1, :p2, :p3, :p4, :p5)""",
            strategy_id,
            tenant_id,
            uuid4(),
            "feedback",
            "running",
            json.dumps({"base_model": base_model}),
        )

        await self._collect_feedback(tenant_id)

        await self._update_stage(strategy_id, "dataset")
        dataset_id = await self._builder.build(tenant_id)

        await self._update_stage(strategy_id, "training")
        run = await self._trainer.train(dataset_id, base_model, tenant_id)

        await self._db.execute(
            "UPDATE harness.evolution_strategies SET training_run_id = :p0 "
            "WHERE id = :p1",
            run.id,
            strategy_id,
        )

        task = asyncio.create_task(
            self._run_governance(strategy_id, run.id, tenant_id)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    async def _run_governance(
        self,
        strategy_id: UUID,
        run_id: UUID,
        tenant_id: UUID,
    ) -> None:
        try:
            while True:
                run = await self._trainer.get_run(run_id)
                if run.status in (
                    TrainingStatus.COMPLETED,
                    TrainingStatus.FAILED,
                    TrainingStatus.CANCELLED,
                ):
                    break
                await asyncio.sleep(self._poll_interval)

            if run.status != TrainingStatus.COMPLETED:
                await self._block(strategy_id, f"training {run.status.value}")
                return

            await self._update_stage(strategy_id, "guardrail")
            safety = await self._guardrail.safety_benchmark(strategy_id, tenant_id)
            if not safety.passed:
                await self._block(
                    strategy_id, f"safety benchmark failed: {safety.reason}"
                )
                return

            perf = await self._guardrail.perf_compare(strategy_id)
            if not perf.passed:
                await self._block(
                    strategy_id, f"perf compare failed: {perf.reason}"
                )
                return

            await self._update_stage(strategy_id, "shadow")
            await self._shadow.start(strategy_id)
            shadow_result = await self._shadow.evaluate(strategy_id)
            await self._shadow.stop(strategy_id)
            if not shadow_result.passed:
                await self._block(
                    strategy_id,
                    f"shadow traffic failed: {shadow_result.reason}",
                )
                return

            await self._update_stage(strategy_id, "approval")
            if self._approval_gate is not None:
                await self._approval_gate.request_approval(
                    strategy_id,
                    run_id,
                    tenant_id,
                    "Strategy passed all gates",
                )
        except Exception as exc:
            await self._block(strategy_id, f"exception: {exc}")

    async def get_status(self, tenant_id: UUID) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT id, training_run_id, stage, stage_status, stage_detail, "
            "created_at FROM harness.evolution_strategies "
            "WHERE tenant_id = :p0 ORDER BY created_at DESC LIMIT 1",
            tenant_id,
        )
        if row is None:
            return {}
        return self._format_strategy(row)

    async def list_strategies(self, tenant_id: UUID) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT id, training_run_id, stage, stage_status, stage_detail, "
            "created_at FROM harness.evolution_strategies "
            "WHERE tenant_id = :p0 ORDER BY created_at DESC",
            tenant_id,
        )
        return [self._format_strategy(r) for r in rows]

    async def get_strategy(self, strategy_id: UUID) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT id, training_run_id, stage, stage_status, stage_detail, "
            "created_at FROM harness.evolution_strategies WHERE id = :p0",
            strategy_id,
        )
        if row is None:
            return {}
        return self._format_strategy(row)

    async def advance_canary(self, strategy_id: UUID) -> None:
        await self._update_stage(strategy_id, "canary")

    async def advance(self, strategy_id: UUID) -> str:
        """Advance a strategy by one step. Returns the new stage.

        Idempotent single-step推进 for worker-driven governance. Handles:
        - ``training``: poll training status; on completion run guardrail,
          on success move to ``shadow``; on failure block.
        - ``shadow``: evaluate shadow traffic; on pass move to ``approval``,
          on fail block.
        - ``canary``: promote to ``full`` (terminal).

        Strategies in ``feedback``/``dataset``/``guardrail``/``approval``
        stages are not advanced by the worker (those are either transient
        or block on human action). Returns the stage after the attempt
        (unchanged if no work was done).
        """
        row = await self._db.fetch_one(
            "SELECT stage, stage_status, training_run_id, tenant_id "
            "FROM harness.evolution_strategies WHERE id = :p0",
            strategy_id,
        )
        if row is None:
            return "unknown"
        stage = row.get("stage")
        status = row.get("stage_status")
        if status != "running":
            return stage or "unknown"

        if stage == "training":
            return await self._advance_from_training(strategy_id, row)
        if stage == "shadow":
            return await self._advance_from_shadow(strategy_id)
        if stage == "canary":
            await self._update_stage(strategy_id, "full", "completed")
            return "full"
        return stage or "unknown"

    async def _advance_from_training(
        self, strategy_id: UUID, row: dict[str, Any]
    ) -> str:
        run_id = row.get("training_run_id")
        if run_id is None:
            await self._block(strategy_id, "no training_run_id linked")
            return "training"
        run = await self._trainer.get_run(run_id)
        if run.status == TrainingStatus.COMPLETED:
            await self._update_stage(strategy_id, "guardrail")
            tenant_id = row.get("tenant_id")
            safety = await self._guardrail.safety_benchmark(strategy_id, tenant_id)
            if not safety.passed:
                await self._block(
                    strategy_id, f"safety benchmark failed: {safety.reason}"
                )
                return "guardrail"
            perf = await self._guardrail.perf_compare(strategy_id)
            if not perf.passed:
                await self._block(
                    strategy_id, f"perf compare failed: {perf.reason}"
                )
                return "guardrail"
            await self._update_stage(strategy_id, "shadow")
            await self._shadow.start(strategy_id)
            return "shadow"
        if run.status in (TrainingStatus.FAILED, TrainingStatus.CANCELLED):
            await self._block(strategy_id, f"training {run.status.value}")
            return "training"
        # Still running — leave as-is.
        return "training"

    async def _advance_from_shadow(self, strategy_id: UUID) -> str:
        result = await self._shadow.evaluate(strategy_id)
        await self._shadow.stop(strategy_id)
        if not result.passed:
            await self._block(
                strategy_id, f"shadow traffic failed: {result.reason}"
            )
            return "shadow"
        await self._update_stage(strategy_id, "approval")
        if self._approval_gate is not None:
            row = await self._db.fetch_one(
                "SELECT training_run_id, tenant_id "
                "FROM harness.evolution_strategies WHERE id = :p0",
                strategy_id,
            )
            if row is not None:
                await self._approval_gate.request_approval(
                    strategy_id,
                    row.get("training_run_id") or strategy_id,
                    row.get("tenant_id") or strategy_id,
                    "Strategy passed all gates",
                )
        return "approval"

    async def rollback(self, strategy_id: UUID) -> None:
        await self._db.execute(
            "UPDATE harness.evolution_strategies "
            "SET stage = :p0, stage_status = :p1 WHERE id = :p2",
            "rolled_back",
            "completed",
            strategy_id,
        )

    async def collect_feedback_only(
        self,
        tenant_id: UUID,
        since_days: int = 7,
    ) -> int:
        return await self._collect_feedback(tenant_id, since_days)

    async def _collect_feedback(
        self,
        tenant_id: UUID,
        since_days: int = 7,
    ) -> int:
        cutoff = datetime.utcnow() - timedelta(days=since_days)
        rows = await self._db.fetch(
            "SELECT DISTINCT session_id FROM trace.spans "
            "WHERE tenant_id = :p0 AND session_id IS NOT NULL "
            "AND start_time >= :p1",
            tenant_id,
            cutoff,
        )
        count = 0
        for row in rows:
            session_id = row.get("session_id")
            if session_id is None:
                continue
            signals = await self._feedback.collect_from_session(session_id)
            if signals:
                await self._feedback.batch_save(signals)
                count += len(signals)
        return count

    async def _update_stage(
        self,
        strategy_id: UUID,
        stage: str,
        status: str = "running",
    ) -> None:
        await self._db.execute(
            "UPDATE harness.evolution_strategies "
            "SET stage = :p0, stage_status = :p1 WHERE id = :p2",
            stage,
            status,
            strategy_id,
        )

    async def _block(
        self,
        strategy_id: UUID,
        reason: str,
    ) -> None:
        await self._db.execute(
            "UPDATE harness.evolution_strategies "
            "SET stage_status = :p0, stage_detail = :p1 WHERE id = :p2",
            "blocked",
            json.dumps({"failure_reason": reason}),
            strategy_id,
        )

    @staticmethod
    def _format_strategy(row: dict[str, Any]) -> dict[str, Any]:
        detail = row.get("stage_detail")
        if isinstance(detail, str):
            detail = json.loads(detail) if detail else {}
        if not isinstance(detail, dict):
            detail = {}
        return {
            "id": row.get("id"),
            "training_run_id": row.get("training_run_id"),
            "stage": row.get("stage"),
            "stage_status": row.get("stage_status"),
            "created_at": row.get("created_at"),
            "detail": detail,
        }
