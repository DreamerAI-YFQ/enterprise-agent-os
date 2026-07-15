"""Tests for evolution admin API routes — auth, run, status, runs, strategies."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.evolution.trainer import TrainingRun, TrainingStatus
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:
    from uuid import UUID


def _config() -> AppConfig:
    return AppConfig(secret_key="test-secret", debug=True)  # type: ignore[call-arg]


def _token(
    config: AppConfig,
    *,
    role: str = "admin",
    tenant_id: UUID | None = None,
) -> str:
    return create_jwt_token(
        secret=config.secret_key,
        user_id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        role=role,
    )


class TestEvolutionAuth:
    async def test_non_admin_returns_403(self) -> None:
        config = _config()
        app = create_app(config)
        app.state.evolution_pipeline = AsyncMock()
        token = _token(config, role="employee")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/evolution/status",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 403

    async def test_not_configured_returns_501(self) -> None:
        config = _config()
        app = create_app(config)
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/evolution/status",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 501


class TestRun:
    async def test_post_run_triggers_pipeline(self) -> None:
        tid = uuid4()
        run = TrainingRun(
            id=uuid4(),
            tenant_id=tid,
            dataset_id=uuid4(),
            base_model="gpt-base",
            status=TrainingStatus.QUEUED,
        )
        pipeline = AsyncMock()
        pipeline.run.return_value = run
        config = _config()
        app = create_app(config)
        app.state.evolution_pipeline = pipeline
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/evolution/run",
                json={"base_model": "gpt-base"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["base_model"] == "gpt-base"
        assert data["status"] == "queued"
        pipeline.run.assert_awaited_once_with(tid, "gpt-base")


class TestStatus:
    async def test_get_status_returns_stage(self) -> None:
        tid = uuid4()
        pipeline = AsyncMock()
        pipeline.get_status.return_value = {
            "id": str(uuid4()),
            "stage": "shadow",
            "stage_status": "running",
            "detail": {"traffic_pct": 10},
        }
        config = _config()
        app = create_app(config)
        app.state.evolution_pipeline = pipeline
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/evolution/status",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["stage"] == "shadow"
        assert data["stage_status"] == "running"
        pipeline.get_status.assert_awaited_once_with(tid)


class TestFeedbackCollect:
    async def test_returns_count(self) -> None:
        tid = uuid4()
        pipeline = AsyncMock()
        pipeline.collect_feedback_only.return_value = 42
        config = _config()
        app = create_app(config)
        app.state.evolution_pipeline = pipeline
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/evolution/feedback/collect",
                json={"since_days": 14},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["count"] == 42
        pipeline.collect_feedback_only.assert_awaited_once_with(tid, since_days=14)


class TestRuns:
    async def test_list_runs(self) -> None:
        tid = uuid4()
        trainer = AsyncMock()
        trainer.list_runs.return_value = [
            TrainingRun(
                id=uuid4(),
                tenant_id=tid,
                base_model="gpt-base",
                status=TrainingStatus.COMPLETED,
            )
        ]
        config = _config()
        app = create_app(config)
        app.state.trainer = trainer
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/evolution/runs",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "completed"

    async def test_list_runs_with_status_filter(self) -> None:
        tid = uuid4()
        trainer = AsyncMock()
        trainer.list_runs.return_value = []
        config = _config()
        app = create_app(config)
        app.state.trainer = trainer
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/evolution/runs?status=completed",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        args = trainer.list_runs.call_args
        assert args.args[1] == TrainingStatus.COMPLETED

    async def test_list_runs_invalid_status(self) -> None:
        config = _config()
        app = create_app(config)
        app.state.trainer = AsyncMock()
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/evolution/runs?status=invalid",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 422

    async def test_get_run_not_found(self) -> None:
        trainer = AsyncMock()
        trainer.get_run.side_effect = KeyError("not found")
        config = _config()
        app = create_app(config)
        app.state.trainer = trainer
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/admin/evolution/runs/{uuid4()}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 404

    async def test_cancel_run(self) -> None:
        run_id = uuid4()
        trainer = AsyncMock()
        trainer.cancel.return_value = None
        config = _config()
        app = create_app(config)
        app.state.trainer = trainer
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/admin/evolution/runs/{run_id}/cancel",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        trainer.cancel.assert_awaited_once_with(run_id)


class TestDatasets:
    async def test_list_datasets(self) -> None:
        from eaos.evolution.dataset import Dataset

        tid = uuid4()
        builder = AsyncMock()
        builder.list_datasets.return_value = [
            Dataset(id=uuid4(), tenant_id=tid, name="ds-1", pair_count=10)
        ]
        config = _config()
        app = create_app(config)
        app.state.dataset_builder = builder
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/evolution/datasets",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "ds-1"

    async def test_get_pairs(self) -> None:
        from eaos.evolution.dataset import PreferencePair

        dataset_id = uuid4()
        builder = AsyncMock()
        builder.get_pairs.return_value = [
            PreferencePair(
                dataset_id=dataset_id,
                prompt="q",
                chosen="good",
                rejected="bad",
            )
        ]
        config = _config()
        app = create_app(config)
        app.state.dataset_builder = builder
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/admin/evolution/datasets/{dataset_id}/pairs?limit=10&offset=0",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["chosen"] == "good"


class TestStrategies:
    async def test_list_strategies(self) -> None:
        tid = uuid4()
        pipeline = AsyncMock()
        pipeline.list_strategies.return_value = [
            {"id": str(uuid4()), "stage": "shadow", "stage_status": "running"}
        ]
        config = _config()
        app = create_app(config)
        app.state.evolution_pipeline = pipeline
        token = _token(config, tenant_id=tid)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/admin/evolution/strategies",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["stage"] == "shadow"

    async def test_get_strategy_not_found(self) -> None:
        pipeline = AsyncMock()
        pipeline.get_strategy.return_value = {}
        config = _config()
        app = create_app(config)
        app.state.evolution_pipeline = pipeline
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/admin/evolution/strategies/{uuid4()}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 404

    async def test_rollback_strategy(self) -> None:
        sid = uuid4()
        pipeline = AsyncMock()
        pipeline.rollback.return_value = None
        config = _config()
        app = create_app(config)
        app.state.evolution_pipeline = pipeline
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/admin/evolution/strategies/{sid}/rollback",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["stage"] == "rolled_back"
        pipeline.rollback.assert_awaited_once_with(sid)

    async def test_advance_canary(self) -> None:
        sid = uuid4()
        pipeline = AsyncMock()
        pipeline.advance_canary.return_value = None
        config = _config()
        app = create_app(config)
        app.state.evolution_pipeline = pipeline
        token = _token(config)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/admin/evolution/strategies/{sid}/canary",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["stage"] == "canary"
        pipeline.advance_canary.assert_awaited_once_with(sid)
