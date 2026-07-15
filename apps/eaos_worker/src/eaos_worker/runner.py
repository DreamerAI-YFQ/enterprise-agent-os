"""Evolution worker — polls pending strategies and advances the governance pipeline.

The worker is a standalone process that periodically queries
``harness.evolution_strategies`` for strategies in ``running`` state at the
``training``, ``shadow``, or ``canary`` stages, and calls
``EvolutionPipelineImpl.advance(strategy_id)`` to move them forward one step.

Start with ``python -m eaos_worker`` (see ``main.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from eaos.core.config import AppConfig
    from eaos_api.wiring import AppDeps

logger = logging.getLogger(__name__)


class _WorkerDb(Protocol):
    """Minimal DB interface: fetch pending strategy rows."""

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]: ...


class _WorkerPipeline(Protocol):
    """Minimal pipeline interface: advance a strategy by one step."""

    async def advance(self, strategy_id: UUID) -> str: ...


_PENDING_SQL = (
    "SELECT id FROM harness.evolution_strategies "
    "WHERE stage_status = 'running' "
    "AND stage IN ('training', 'shadow', 'canary')"
)


async def run_worker(
    db: _WorkerDb,
    pipeline: _WorkerPipeline,
    *,
    poll_interval: float = 60.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Main worker loop.

    Polls ``evolution_strategies`` for pending strategies and advances each
    via ``pipeline.advance(strategy_id)``. Loops until ``stop_event`` is set
    (or the task is cancelled if no ``stop_event`` is provided).

    Exceptions from individual ``advance()`` calls are logged and swallowed so
    one failing strategy doesn't kill the worker.
    """
    logger.info("Worker started (poll_interval=%ss)", poll_interval)
    while _should_continue(stop_event):
        count = await _advance_pending(db, pipeline)
        if count > 0:
            logger.info("Advanced %d strategies", count)
        await _sleep_or_stop(poll_interval, stop_event)
    logger.info("Worker stopped")


def _should_continue(stop_event: asyncio.Event | None) -> bool:
    return stop_event is None or not stop_event.is_set()


async def _sleep_or_stop(
    duration: float, stop_event: asyncio.Event | None
) -> None:
    """Sleep for ``duration``, but return early if ``stop_event`` is set."""
    if stop_event is None:
        await asyncio.sleep(duration)
        return
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=duration)


async def _advance_pending(db: _WorkerDb, pipeline: _WorkerPipeline) -> int:
    """Query pending strategies and advance each. Returns count advanced."""
    rows = await db.fetch(_PENDING_SQL)
    count = 0
    for row in rows:
        strategy_id = _parse_uuid(row.get("id"))
        if strategy_id is None:
            continue
        try:
            stage = await pipeline.advance(strategy_id)
            logger.info("Strategy %s -> %s", strategy_id, stage)
            count += 1
        except Exception:
            logger.exception("Failed to advance strategy %s", strategy_id)
    return count


def _parse_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


async def main_async(
    config: AppConfig,
    *,
    poll_interval: float = 60.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Build production deps and run the worker until interrupted."""
    from eaos_api.wiring import build_deps, close_deps

    deps: AppDeps = await build_deps(config)
    try:
        await run_worker(
            deps.db,
            deps.evolution_pipeline,
            poll_interval=poll_interval,
            stop_event=stop_event,
        )
    finally:
        await close_deps(deps)
