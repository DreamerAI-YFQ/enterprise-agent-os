"""Evolution admin API routes — run, status, runs, datasets, strategies.

All /admin/evolution/* routes require the admin role. Evolution components
(evolution_pipeline, trainer, dataset_builder) are read from app.state.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast
from uuid import UUID  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002 — runtime for FastAPI
from eaos.gateway.api.routes.admin import require_admin
from fastapi import APIRouter, Depends, HTTPException, Query, Request  # noqa: TC002
from pydantic import BaseModel

router = APIRouter(prefix="/admin/evolution", tags=["admin/evolution"])


def _component(request: Request, name: str) -> Any:
    """Fetch a component from app.state; 501 if not wired."""
    component = getattr(request.app.state, name, None)
    if component is None:
        raise HTTPException(
            status_code=501,
            detail=f"{name} not configured on this instance",
        )
    return component


class RunRequest(BaseModel):
    base_model: str


class FeedbackCollectRequest(BaseModel):
    since_days: int = 7


# -- Run / Status / Feedback ------------------------------------------------


@router.post("/run")
async def run_evolution(
    body: RunRequest,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    pipeline = _component(request, "evolution_pipeline")
    run = await pipeline.run(principal.tenant_id, body.base_model)
    return asdict(run)


@router.get("/status")
async def get_status(
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    pipeline = _component(request, "evolution_pipeline")
    return cast("dict[str, Any]", await pipeline.get_status(principal.tenant_id))


@router.post("/feedback/collect")
async def collect_feedback(
    body: FeedbackCollectRequest,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, int]:
    pipeline = _component(request, "evolution_pipeline")
    count = await pipeline.collect_feedback_only(
        principal.tenant_id, since_days=body.since_days
    )
    return {"count": count}


# -- Training Runs ----------------------------------------------------------


@router.get("/runs")
async def list_runs(
    request: Request,
    status: str | None = Query(default=None),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, Any]]:
    from eaos.evolution.trainer import TrainingStatus

    trainer = _component(request, "trainer")
    status_filter: TrainingStatus | None = None
    if status is not None:
        valid = {s.value: s for s in TrainingStatus}
        status_filter = valid.get(status)
        if status_filter is None:
            raise HTTPException(
                status_code=422, detail=f"invalid status: {status}"
            )
    runs = await trainer.list_runs(principal.tenant_id, status_filter)
    return [asdict(r) for r in runs]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    trainer = _component(request, "trainer")
    try:
        run = await trainer.get_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found") from None
    return asdict(run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    trainer = _component(request, "trainer")
    await trainer.cancel(run_id)
    return {"id": str(run_id), "status": "cancelled"}


# -- Datasets ---------------------------------------------------------------


@router.get("/datasets")
async def list_datasets(
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, Any]]:
    builder = _component(request, "dataset_builder")
    datasets = await builder.list_datasets(principal.tenant_id)
    return [asdict(d) for d in datasets]


@router.get("/datasets/{dataset_id}/pairs")
async def get_pairs(
    dataset_id: UUID,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, Any]]:
    builder = _component(request, "dataset_builder")
    pairs = await builder.get_pairs(dataset_id, limit=limit, offset=offset)
    return [asdict(p) for p in pairs]


# -- Strategies -------------------------------------------------------------


@router.get("/strategies")
async def list_strategies(
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, Any]]:
    pipeline = _component(request, "evolution_pipeline")
    return cast(
        "list[dict[str, Any]]", await pipeline.list_strategies(principal.tenant_id)
    )


@router.get("/strategies/{strategy_id}")
async def get_strategy(
    strategy_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    pipeline = _component(request, "evolution_pipeline")
    result = await pipeline.get_strategy(strategy_id)
    if not result:
        raise HTTPException(status_code=404, detail="strategy not found")
    return cast("dict[str, Any]", result)


@router.post("/strategies/{strategy_id}/canary")
async def advance_canary(
    strategy_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    pipeline = _component(request, "evolution_pipeline")
    await pipeline.advance_canary(strategy_id)
    return {"id": str(strategy_id), "stage": "canary"}


@router.post("/strategies/{strategy_id}/rollback")
async def rollback_strategy(
    strategy_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    pipeline = _component(request, "evolution_pipeline")
    await pipeline.rollback(strategy_id)
    return {"id": str(strategy_id), "stage": "rolled_back"}
