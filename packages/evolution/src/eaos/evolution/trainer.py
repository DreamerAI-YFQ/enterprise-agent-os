"""DPO trainer — Direct Preference Optimization using trl library.

Offline RL: no online exploration (unsafe in enterprise). Learns from
historical traces via preference pairs. The trained strategy must pass
Harness evolution governance (six-step pipeline) before going live.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from datetime import datetime

    from eaos.evolution.artifact_store import ArtifactStore
    from eaos.evolution.dataset import PreferenceDatasetBuilder


class TrainingStatus(StrEnum):
    """Training run status."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TrainingRun:
    """A DPO training run record."""

    id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default=UUID(int=0))
    dataset_id: UUID = field(default=UUID(int=0))
    base_model: str = ""
    method: str = "dpo"
    status: TrainingStatus = TrainingStatus.QUEUED
    metrics: dict[str, Any] = field(default_factory=dict)  # loss, accuracy, reward_margin
    model_artifact_path: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class DPOTrainer(Protocol):
    """DPO training via trl library."""

    async def train(
        self,
        dataset_id: UUID,
        base_model: str,
        tenant_id: UUID,
        *,
        learning_rate: float = 5e-7,
        batch_size: int = 4,
        epochs: int = 1,
    ) -> TrainingRun:
        """Start a DPO training run. Returns immediately with queued status."""
        ...

    async def get_run(self, run_id: UUID) -> TrainingRun:
        """Fetch training run status."""
        ...

    async def cancel(self, run_id: UUID) -> None:
        """Cancel a running/queued training."""
        ...

    async def list_runs(
        self,
        tenant_id: UUID,
        status: TrainingStatus | None = None,
    ) -> list[TrainingRun]:
        """List training runs for a tenant."""
        ...


class TrainerDb(Protocol):
    """Minimal DB subset for training run persistence."""

    async def execute(self, sql: str, *params: Any) -> None: ...

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]: ...

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...


TrainFn = Callable[..., tuple[Any, dict[str, Any]]]
"""Injectable sync training function. Returns (model_artifact_path, metrics).

Defaults to _sync_dpo_train in _worker.py. Tests inject a mock to avoid
loading trl/torch at test time.
"""


def _parse_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_to_run(row: dict[str, Any]) -> TrainingRun:
    metrics_raw = _parse_jsonb(row.get("metrics") or {})
    metrics: dict[str, Any] = metrics_raw if isinstance(metrics_raw, dict) else {}
    error = metrics.get("error") if isinstance(metrics.get("error"), str) else None
    return TrainingRun(
        id=row["id"],
        tenant_id=row["tenant_id"],
        dataset_id=row["dataset_id"],
        base_model=row["base_model"],
        method=row["method"],
        status=TrainingStatus(row["status"]),
        metrics=metrics,
        model_artifact_path=row.get("model_artifact_path"),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        error=error,
    )


_TRAINING_RUN_COLUMNS = (
    "id, tenant_id, dataset_id, base_model, method, status, metrics, "
    "model_artifact_path, started_at, completed_at"
)


class DPOTrainerImpl:
    """DPOTrainer backed by evolution.training_runs.

    train() inserts a queued row and spawns a background asyncio task that
    invokes the (injectable) train_fn via asyncio.to_thread — trl is sync and
    heavy, so it must not block the event loop. Errors are stored in
    metrics["error"] JSONB (no dedicated error column on the table).

    If ``artifact_store`` is provided, the training worker uploads the model
    artifact to the store (S3/OSS/local) and stores the returned URI in
    ``model_artifact_path`` instead of the local filesystem path.
    """

    def __init__(
        self,
        db: TrainerDb,
        builder: PreferenceDatasetBuilder | None = None,
        train_fn: TrainFn | None = None,
        artifact_dir: str = "",
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._db = db
        self._builder = builder
        self._train_fn = train_fn
        self._artifact_dir = artifact_dir or os.environ.get(
            "EAOS_MODEL_ARTIFACT_DIR", ""
        )
        self._artifact_store = artifact_store
        self._tasks: set[asyncio.Task[None]] = set()

    async def train(
        self,
        dataset_id: UUID,
        base_model: str,
        tenant_id: UUID,
        *,
        learning_rate: float = 5e-7,
        batch_size: int = 4,
        epochs: int = 1,
    ) -> TrainingRun:
        run_id = uuid4()
        await self._db.execute(
            """INSERT INTO evolution.training_runs
               (id, tenant_id, dataset_id, base_model, method, status, metrics)
               VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6)""",
            run_id,
            tenant_id,
            dataset_id,
            base_model,
            "dpo",
            TrainingStatus.QUEUED.value,
            json.dumps({}),
        )
        run = TrainingRun(
            id=run_id,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            base_model=base_model,
            method="dpo",
            status=TrainingStatus.QUEUED,
        )

        from eaos.evolution._worker import run_training

        task = asyncio.create_task(
            run_training(
                run_id=run_id,
                dataset_id=dataset_id,
                tenant_id=tenant_id,
                base_model=base_model,
                db=self._db,
                builder=self._builder,
                train_fn=self._train_fn,
                artifact_dir=self._artifact_dir,
                learning_rate=learning_rate,
                batch_size=batch_size,
                epochs=epochs,
                artifact_store=self._artifact_store,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    async def get_run(self, run_id: UUID) -> TrainingRun:
        row = await self._db.fetch_one(
            f"SELECT {_TRAINING_RUN_COLUMNS} FROM evolution.training_runs "
            "WHERE id = :p0",
            run_id,
        )
        if row is None:
            raise KeyError(f"Training run {run_id} not found")
        return _row_to_run(row)

    async def cancel(self, run_id: UUID) -> None:
        await self._db.execute(
            "UPDATE evolution.training_runs SET status = :p0 "
            "WHERE id = :p1 "
            "AND (status = :p2 OR status = :p3)",
            TrainingStatus.CANCELLED.value,
            run_id,
            TrainingStatus.QUEUED.value,
            TrainingStatus.RUNNING.value,
        )

    async def list_runs(
        self,
        tenant_id: UUID,
        status: TrainingStatus | None = None,
    ) -> list[TrainingRun]:
        if status is None:
            rows = await self._db.fetch(
                f"SELECT {_TRAINING_RUN_COLUMNS} FROM evolution.training_runs "
                "WHERE tenant_id = :p0 ORDER BY started_at DESC NULLS LAST",
                tenant_id,
            )
        else:
            rows = await self._db.fetch(
                f"SELECT {_TRAINING_RUN_COLUMNS} FROM evolution.training_runs "
                "WHERE tenant_id = :p0 AND status = :p1 "
                "ORDER BY started_at DESC NULLS LAST",
                tenant_id,
                status.value,
            )
        return [_row_to_run(r) for r in rows]
