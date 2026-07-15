"""Async worker for DPO training — bridges async DB with sync trl.

run_training is spawned as a background task by DPOTrainerImpl.train().
The heavy trl/torch work runs in asyncio.to_thread so it doesn't block the
event loop. train_fn is injectable for testing (mock trl without loading it).

If an ``artifact_store`` is provided, the local model artifact directory is
uploaded to the store (S3/OSS/local) after training completes, and the
returned URI replaces the local path in ``model_artifact_path``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from eaos.evolution.artifact_store import ArtifactStore
    from eaos.evolution.dataset import PreferenceDatasetBuilder, PreferencePair
    from eaos.evolution.trainer import TrainerDb


async def run_training(
    *,
    run_id: UUID,
    dataset_id: UUID,
    tenant_id: UUID,
    base_model: str,
    db: TrainerDb,
    builder: PreferenceDatasetBuilder | None,
    train_fn: Callable[..., tuple[Any, dict[str, Any]]] | None,
    artifact_dir: str,
    learning_rate: float,
    batch_size: int,
    epochs: int,
    artifact_store: ArtifactStore | None = None,
) -> None:
    """Background training task: queued -> running -> completed/failed.

    On success: updates status=completed, metrics, model_artifact_path. If
    ``artifact_store`` is provided, the local artifact is uploaded and the
    returned URI is stored instead of the local path.
    On failure: updates status=failed, metrics={"error": str(exc)}.
    """
    await db.execute(
        "UPDATE evolution.training_runs SET status = :p0, started_at = NOW() "
        "WHERE id = :p1",
        "running",
        run_id,
    )
    try:
        if builder is None:
            raise RuntimeError("PreferenceDatasetBuilder not configured")
        pairs = await builder.get_pairs(dataset_id)
        if not pairs:
            raise RuntimeError(f"No preference pairs in dataset {dataset_id}")

        fn = train_fn or _sync_dpo_train
        local_artifact_path, raw_metrics = await asyncio.to_thread(
            fn,
            pairs=pairs,
            base_model=base_model,
            artifact_dir=artifact_dir,
            run_id=run_id,
            learning_rate=learning_rate,
            batch_size=batch_size,
            epochs=epochs,
        )

        clean_metrics: dict[str, Any] = {}
        for k, v in raw_metrics.items():
            try:
                clean_metrics[k] = float(v)
            except (TypeError, ValueError):
                clean_metrics[k] = str(v)

        if artifact_store is not None:
            artifact_uri = await artifact_store.save(
                run_id, Path(local_artifact_path)
            )
        else:
            artifact_uri = local_artifact_path

        await db.execute(
            "UPDATE evolution.training_runs "
            "SET status = :p0, metrics = :p1, model_artifact_path = :p2, "
            "completed_at = NOW() WHERE id = :p3",
            "completed",
            json.dumps(clean_metrics),
            artifact_uri,
            run_id,
        )
    except Exception as exc:
        await db.execute(
            "UPDATE evolution.training_runs "
            "SET status = :p0, metrics = :p1, completed_at = NOW() "
            "WHERE id = :p2",
            "failed",
            json.dumps({"error": str(exc)}),
            run_id,
        )


def _sync_dpo_train(
    *,
    pairs: list[PreferencePair],
    base_model: str,
    artifact_dir: str,
    run_id: UUID,
    learning_rate: float,
    batch_size: int,
    epochs: int,
) -> tuple[str, dict[str, Any]]:
    """Synchronous DPO training via trl. Lazy import so tests don't load torch."""
    from trl import DPOConfig, DPOTrainer  # type: ignore[attr-defined]

    ds = _to_trl_dataset(pairs)
    run_dir = os.path.join(artifact_dir or ".", str(run_id))
    os.makedirs(run_dir, exist_ok=True)

    config = DPOConfig(
        output_dir=run_dir,
        learning_rate=learning_rate,
        per_device_train_batch_size=batch_size,
        num_train_epochs=epochs,
        remove_unused_columns=False,
    )
    trainer = DPOTrainer(
        model=base_model,
        args=config,
        train_dataset=ds,
    )
    train_result = trainer.train()
    trainer.save_model(run_dir)
    return run_dir, dict(train_result.metrics)


def _to_trl_dataset(pairs: list[PreferencePair]) -> Any:
    """Convert PreferencePair list to a trl-compatible datasets.Dataset."""
    from datasets import Dataset  # type: ignore[import-untyped]

    rows = [
        {
            "prompt": p.prompt,
            "chosen": p.chosen,
            "rejected": p.rejected,
        }
        for p in pairs
    ]
    return Dataset.from_list(rows)
