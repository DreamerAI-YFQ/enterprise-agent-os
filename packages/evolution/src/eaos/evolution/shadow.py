"""Shadow traffic manager — stage 3 of evolution governance.

Runs the new RL strategy on a small percentage of traffic in parallel with
the baseline, comparing metrics without affecting users. If metrics degrade
beyond threshold, the strategy is blocked from advancing.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from eaos.evolution.guardrail import GuardrailResult

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.evolution.replay import TraceReplayer


class ShadowTrafficManager(Protocol):
    """Shadow traffic for RL strategy validation (stage 3)."""

    async def start(
        self,
        strategy_id: UUID,
        traffic_pct: int = 10,
        duration_hours: int = 24,
    ) -> None:
        """Start shadow traffic: route traffic_pct% of requests to new strategy.

        Results are compared against baseline but NOT shown to users.
        """
        ...

    async def evaluate(self, strategy_id: UUID) -> GuardrailResult:
        """Evaluate shadow traffic results after duration.

        Compares: adoption rate, error rate, latency, cost. Passes if new
        strategy is not significantly worse than baseline.
        """
        ...

    async def stop(self, strategy_id: UUID) -> None:
        """Stop shadow traffic (manual or after evaluation)."""
        ...

    async def get_status(self, strategy_id: UUID) -> dict[str, Any]:
        """Get current shadow traffic status and interim metrics."""
        ...


class ShadowDb(Protocol):
    """Minimal DB subset for shadow traffic state persistence."""

    async def execute(self, sql: str, *params: Any) -> None: ...

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...


def _parse_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


_HIGHER_IS_BETTER = frozenset({"adoption_rate"})

_DEFAULT_THRESHOLDS: dict[str, float] = {
    "error_rate": 1.1,
    "avg_latency_ms": 1.2,
    "cost_usd": 1.3,
    "adoption_rate": 0.9,
}


class ShadowTrafficManagerImpl:
    """ShadowTrafficManager backed by harness.evolution_strategies.

    Phase 5 simplified: no real traffic routing. The pipeline (T6) replays
    historical traces using the new strategy and stores results in
    stage_detail.shadow_results. This class manages state and evaluates
    metrics against thresholds.
    """

    def __init__(
        self,
        db: ShadowDb,
        thresholds: dict[str, float] | None = None,
        replayer: TraceReplayer | None = None,
    ) -> None:
        self._db = db
        self._thresholds = thresholds or dict(_DEFAULT_THRESHOLDS)
        self._replayer = replayer

    async def start(
        self,
        strategy_id: UUID,
        traffic_pct: int = 10,
        duration_hours: int = 24,
    ) -> None:
        detail: dict[str, Any] = {
            "traffic_pct": traffic_pct,
            "duration_hours": duration_hours,
            "started_at": datetime.utcnow().isoformat(),
        }
        await self._db.execute(
            "UPDATE harness.evolution_strategies "
            "SET stage = :p0, stage_status = :p1, stage_detail = :p2 "
            "WHERE id = :p3",
            "shadow",
            "running",
            json.dumps(detail),
            strategy_id,
        )

        if self._replayer is not None:
            await self._replay_and_merge(strategy_id, detail)

    async def _replay_and_merge(
        self,
        strategy_id: UUID,
        detail: dict[str, Any],
    ) -> None:
        """Query strategy + training_run for model info, replay, merge metrics."""
        replayer = self._replayer
        if replayer is None:
            return
        row = await self._db.fetch_one(
            "SELECT es.tenant_id, tr.base_model, tr.model_artifact_path "
            "FROM harness.evolution_strategies es "
            "LEFT JOIN evolution.training_runs tr ON tr.id = es.training_run_id "
            "WHERE es.id = :p0",
            strategy_id,
        )
        if row is None:
            return
        tenant_id = row.get("tenant_id")
        baseline_model = str(row.get("base_model") or "")
        artifact = row.get("model_artifact_path")
        new_model = (
            str(artifact)
            if artifact and "/" not in str(artifact) and "\\" not in str(artifact)
            else baseline_model
        )
        if tenant_id is None or not baseline_model or not new_model:
            return

        baseline_metrics, shadow_results = await replayer.replay(
            strategy_id=strategy_id,
            tenant_id=tenant_id,
            baseline_model=baseline_model,
            new_model=new_model,
        )
        detail["baseline_metrics"] = baseline_metrics
        detail["shadow_results"] = shadow_results
        await self._db.execute(
            "UPDATE harness.evolution_strategies "
            "SET stage_detail = :p0 WHERE id = :p1",
            json.dumps(detail),
            strategy_id,
        )

    async def evaluate(self, strategy_id: UUID) -> GuardrailResult:
        row = await self._db.fetch_one(
            "SELECT stage_detail FROM harness.evolution_strategies WHERE id = :p0",
            strategy_id,
        )
        if row is None:
            raise KeyError(f"Strategy {strategy_id} not found")
        detail = _parse_jsonb(row.get("stage_detail") or {})
        if not isinstance(detail, dict):
            detail = {}
        baseline = detail.get("baseline_metrics") or {}
        shadow = detail.get("shadow_results") or {}
        if not baseline or not shadow:
            return GuardrailResult(
                passed=False,
                reason="Missing baseline_metrics or shadow_results in stage_detail",
                details={"thresholds": self._thresholds},
                checked_at=datetime.utcnow(),
            )

        comparisons: dict[str, Any] = {}
        all_pass = True
        for key, threshold in self._thresholds.items():
            base_val = baseline.get(key)
            new_val = shadow.get(key)
            if base_val is None or new_val is None:
                continue
            if key in _HIGHER_IS_BETTER:
                passed = float(new_val) >= float(base_val) * threshold
            else:
                passed = float(new_val) <= float(base_val) * threshold
            comparisons[key] = {
                "baseline": base_val,
                "new": new_val,
                "threshold": threshold,
                "passed": passed,
            }
            if not passed:
                all_pass = False

        return GuardrailResult(
            passed=all_pass,
            reason=(
                "Shadow metrics within thresholds"
                if all_pass
                else "Some shadow metrics degraded beyond threshold"
            ),
            details={
                "metrics": comparisons,
                "thresholds": self._thresholds,
            },
            checked_at=datetime.utcnow(),
        )

    async def stop(self, strategy_id: UUID) -> None:
        await self._db.execute(
            "UPDATE harness.evolution_strategies "
            "SET stage_status = :p0 WHERE id = :p1",
            "completed",
            strategy_id,
        )

    async def get_status(self, strategy_id: UUID) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT stage, stage_status, stage_detail "
            "FROM harness.evolution_strategies WHERE id = :p0",
            strategy_id,
        )
        if row is None:
            return {}
        detail = _parse_jsonb(row.get("stage_detail") or {})
        if not isinstance(detail, dict):
            detail = {}
        return {
            "stage": row.get("stage"),
            "stage_status": row.get("stage_status"),
            **detail,
        }
