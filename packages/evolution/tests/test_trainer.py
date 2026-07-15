"""Tests for DPOTrainerImpl and background training worker."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from eaos.evolution._worker import run_training
from eaos.evolution.dataset import Dataset, PreferencePair
from eaos.evolution.trainer import DPOTrainerImpl, TrainingStatus


class _MockDb:
    def __init__(self) -> None:
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self._fetch_result: list[dict[str, Any]] = []
        self._fetch_one_result: dict[str, Any] | None = None

    async def execute(self, sql: str, *params: Any) -> None:
        self.executes.append((sql, params))

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        return list(self._fetch_result)

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        return self._fetch_one_result


class _MockBuilder:
    def __init__(self, pairs: list[PreferencePair] | None = None) -> None:
        self._pairs = pairs or []

    async def get_pairs(
        self,
        dataset_id: UUID,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[PreferencePair]:
        return list(self._pairs)

    async def build(self, tenant_id: UUID, name: str | None = None) -> UUID:
        return uuid4()

    async def get_dataset(self, dataset_id: UUID) -> Dataset:
        raise KeyError(dataset_id)

    async def list_datasets(self, tenant_id: UUID) -> list[Dataset]:
        return []

    async def validate_pair(self, pair: PreferencePair) -> bool:
        return True


def _mock_train_fn(**kwargs: Any) -> tuple[str, dict[str, Any]]:
    return ("/artifacts/run-1", {"loss": 0.5, "accuracy": 0.9})


def _failing_train_fn(**kwargs: Any) -> tuple[str, dict[str, Any]]:
    raise RuntimeError("training crashed")


def _make_pair(
    prompt: str = "hello",
    chosen: str = "hi",
    rejected: str = "hey",
) -> PreferencePair:
    return PreferencePair(prompt=prompt, chosen=chosen, rejected=rejected)


def _make_run_row(
    *,
    run_id: UUID | None = None,
    status: str = "completed",
    metrics: dict[str, Any] | None = None,
    artifact_path: str | None = "/artifacts/run-1",
) -> dict[str, Any]:
    return {
        "id": run_id or uuid4(),
        "tenant_id": uuid4(),
        "dataset_id": uuid4(),
        "base_model": "gpt2",
        "method": "dpo",
        "status": status,
        "metrics": json.dumps(metrics or {"loss": 0.3}),
        "model_artifact_path": artifact_path,
        "started_at": None,
        "completed_at": None,
    }


class TestDPOTrainerImpl:
    async def test_train_returns_queued_run(self) -> None:
        db = _MockDb()
        builder = _MockBuilder(pairs=[_make_pair()])
        trainer = DPOTrainerImpl(db, builder, train_fn=_mock_train_fn)
        run = await trainer.train(uuid4(), "gpt2", uuid4())
        assert run.status == TrainingStatus.QUEUED
        assert run.method == "dpo"
        assert run.base_model == "gpt2"
        await asyncio.gather(*trainer._tasks, return_exceptions=True)

    async def test_train_inserts_queued_row(self) -> None:
        db = _MockDb()
        builder = _MockBuilder(pairs=[_make_pair()])
        trainer = DPOTrainerImpl(db, builder, train_fn=_mock_train_fn)
        run = await trainer.train(uuid4(), "gpt2", uuid4())
        sql, params = db.executes[0]
        assert "INSERT INTO evolution.training_runs" in sql
        assert params[0] == run.id
        assert params[4] == "dpo"
        assert params[5] == "queued"
        await asyncio.gather(*trainer._tasks, return_exceptions=True)

    async def test_get_run_returns_run(self) -> None:
        db = _MockDb()
        run_id = uuid4()
        db._fetch_one_result = _make_run_row(run_id=run_id, status="completed")
        trainer = DPOTrainerImpl(db)
        run = await trainer.get_run(run_id)
        assert run.id == run_id
        assert run.status == TrainingStatus.COMPLETED
        assert run.metrics == {"loss": 0.3}

    async def test_get_run_raises_on_not_found(self) -> None:
        db = _MockDb()
        db._fetch_one_result = None
        trainer = DPOTrainerImpl(db)
        with pytest.raises(KeyError, match="not found"):
            await trainer.get_run(uuid4())

    async def test_cancel_updates_status(self) -> None:
        db = _MockDb()
        trainer = DPOTrainerImpl(db)
        run_id = uuid4()
        await trainer.cancel(run_id)
        sql, params = db.executes[-1]
        assert "UPDATE evolution.training_runs" in sql
        assert params[0] == "cancelled"
        assert params[1] == run_id

    async def test_list_runs_no_filter(self) -> None:
        db = _MockDb()
        db._fetch_result = [
            _make_run_row(status="completed"),
            _make_run_row(status="failed"),
        ]
        trainer = DPOTrainerImpl(db)
        runs = await trainer.list_runs(uuid4())
        assert len(runs) == 2
        assert runs[0].status == TrainingStatus.COMPLETED
        assert runs[1].status == TrainingStatus.FAILED

    async def test_list_runs_with_status_filter(self) -> None:
        db = _MockDb()
        db._fetch_result = [_make_run_row(status="completed")]
        trainer = DPOTrainerImpl(db)
        runs = await trainer.list_runs(uuid4(), status=TrainingStatus.COMPLETED)
        assert len(runs) == 1
        assert runs[0].status == TrainingStatus.COMPLETED


class TestWorker:
    async def test_run_training_success(self) -> None:
        db = _MockDb()
        builder = _MockBuilder(pairs=[_make_pair()])
        await run_training(
            run_id=uuid4(),
            dataset_id=uuid4(),
            tenant_id=uuid4(),
            base_model="gpt2",
            db=db,
            builder=builder,
            train_fn=_mock_train_fn,
            artifact_dir="/tmp",
            learning_rate=5e-7,
            batch_size=4,
            epochs=1,
        )
        assert db.executes[0][1][0] == "running"
        assert db.executes[1][1][0] == "completed"
        metrics = json.loads(db.executes[1][1][1])
        assert metrics["loss"] == 0.5

    async def test_run_training_failure(self) -> None:
        db = _MockDb()
        builder = _MockBuilder(pairs=[_make_pair()])
        await run_training(
            run_id=uuid4(),
            dataset_id=uuid4(),
            tenant_id=uuid4(),
            base_model="gpt2",
            db=db,
            builder=builder,
            train_fn=_failing_train_fn,
            artifact_dir="/tmp",
            learning_rate=5e-7,
            batch_size=4,
            epochs=1,
        )
        assert db.executes[0][1][0] == "running"
        assert db.executes[1][1][0] == "failed"
        metrics = json.loads(db.executes[1][1][1])
        assert "error" in metrics
        assert "training crashed" in metrics["error"]

    async def test_run_training_no_builder(self) -> None:
        db = _MockDb()
        await run_training(
            run_id=uuid4(),
            dataset_id=uuid4(),
            tenant_id=uuid4(),
            base_model="gpt2",
            db=db,
            builder=None,
            train_fn=_mock_train_fn,
            artifact_dir="/tmp",
            learning_rate=5e-7,
            batch_size=4,
            epochs=1,
        )
        assert db.executes[1][1][0] == "failed"
        metrics = json.loads(db.executes[1][1][1])
        assert "PreferenceDatasetBuilder not configured" in metrics["error"]

    async def test_run_training_no_pairs(self) -> None:
        db = _MockDb()
        builder = _MockBuilder(pairs=[])
        await run_training(
            run_id=uuid4(),
            dataset_id=uuid4(),
            tenant_id=uuid4(),
            base_model="gpt2",
            db=db,
            builder=builder,
            train_fn=_mock_train_fn,
            artifact_dir="/tmp",
            learning_rate=5e-7,
            batch_size=4,
            epochs=1,
        )
        assert db.executes[1][1][0] == "failed"
        metrics = json.loads(db.executes[1][1][1])
        assert "No preference pairs" in metrics["error"]

    async def test_run_training_uses_injected_train_fn(self) -> None:
        db = _MockDb()
        builder = _MockBuilder(pairs=[_make_pair()])
        called_with: dict[str, Any] = {}

        def _tracking_fn(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            called_with.update(kwargs)
            return ("/path", {"reward": 1.0})

        await run_training(
            run_id=uuid4(),
            dataset_id=uuid4(),
            tenant_id=uuid4(),
            base_model="gpt2",
            db=db,
            builder=builder,
            train_fn=_tracking_fn,
            artifact_dir="/tmp",
            learning_rate=1e-5,
            batch_size=8,
            epochs=3,
        )
        assert called_with["base_model"] == "gpt2"
        assert called_with["learning_rate"] == 1e-5
        assert called_with["batch_size"] == 8
        assert called_with["epochs"] == 3
        assert len(called_with["pairs"]) == 1
