"""T2 worker tests — run_worker loop behavior with fake deps.

Unit tests use lightweight fakes satisfying the _WorkerDb / _WorkerPipeline
Protocols; no live PG or build_deps needed. Integration coverage of main_async
(build_deps + run_worker against real PG) lives in M6 integration tests.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from eaos_worker.runner import _advance_pending, run_worker


class _FakeDb:
    """Fake DB satisfying _WorkerDb. Returns canned rows; sets stop_event after N fetches."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        stop_event: asyncio.Event | None = None,
        stop_after: int = 1,
    ) -> None:
        self._rows = rows
        self._stop_event = stop_event
        self._stop_after = stop_after
        self.fetch_count = 0

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        del sql, params  # unused
        self.fetch_count += 1
        if self._stop_event is not None and self.fetch_count >= self._stop_after:
            self._stop_event.set()
        return self._rows


class _FakePipeline:
    """Fake pipeline satisfying _WorkerPipeline. Records advance() calls."""

    def __init__(
        self,
        *,
        result: str = "shadow",
        raise_on_advance: bool = False,
    ) -> None:
        self._result = result
        self._raise = raise_on_advance
        self.advanced: list[UUID] = []

    async def advance(self, strategy_id: UUID) -> str:
        if self._raise:
            raise RuntimeError("advance failed")
        self.advanced.append(strategy_id)
        return self._result


class TestRunWorker:
    async def test_advances_pending_strategies(self) -> None:
        sid1, sid2 = uuid4(), uuid4()
        stop = asyncio.Event()
        db = _FakeDb(
            [{"id": sid1}, {"id": sid2}],
            stop_event=stop,
            stop_after=1,
        )
        pipeline = _FakePipeline()

        await run_worker(db, pipeline, poll_interval=0.01, stop_event=stop)

        assert pipeline.advanced == [sid1, sid2]

    async def test_idle_when_no_pending(self) -> None:
        stop = asyncio.Event()
        db = _FakeDb([], stop_event=stop, stop_after=1)
        pipeline = _FakePipeline()

        await run_worker(db, pipeline, poll_interval=0.01, stop_event=stop)

        assert pipeline.advanced == []

    async def test_stops_immediately_when_stop_event_set(self) -> None:
        stop = asyncio.Event()
        stop.set()
        db = _FakeDb([{"id": uuid4()}])
        pipeline = _FakePipeline()

        await run_worker(db, pipeline, poll_interval=0.01, stop_event=stop)

        assert db.fetch_count == 0
        assert pipeline.advanced == []

    async def test_advance_exception_swallowed(self) -> None:
        sid = uuid4()
        stop = asyncio.Event()
        db = _FakeDb(
            [{"id": sid}],
            stop_event=stop,
            stop_after=1,
        )
        pipeline = _FakePipeline(raise_on_advance=True)

        await run_worker(db, pipeline, poll_interval=0.01, stop_event=stop)

        # Worker didn't crash despite advance raising; fetch was called.
        assert db.fetch_count >= 1

    async def test_multiple_iterations(self) -> None:
        sid = uuid4()
        stop = asyncio.Event()
        db = _FakeDb(
            [{"id": sid}],
            stop_event=stop,
            stop_after=3,
        )
        pipeline = _FakePipeline(result="training")

        await run_worker(db, pipeline, poll_interval=0.001, stop_event=stop)

        assert db.fetch_count == 3
        assert len(pipeline.advanced) == 3

    async def test_runs_forever_without_stop_event_until_cancelled(self) -> None:
        """Without stop_event, worker loops forever; cancellation stops it."""
        sid = uuid4()
        db = _FakeDb([{"id": sid}])
        pipeline = _FakePipeline()

        task = asyncio.create_task(
            run_worker(db, pipeline, poll_interval=0.001)
        )
        await asyncio.sleep(0.02)  # let a few iterations run
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(pipeline.advanced) >= 1


class TestAdvancePending:
    async def test_returns_count_advanced(self) -> None:
        sid1, sid2, sid3 = uuid4(), uuid4(), uuid4()
        db = _FakeDb([{"id": sid1}, {"id": sid2}, {"id": sid3}])
        pipeline = _FakePipeline()

        count = await _advance_pending(db, pipeline)

        assert count == 3
        assert pipeline.advanced == [sid1, sid2, sid3]

    async def test_skips_rows_with_null_id(self) -> None:
        sid = uuid4()
        db = _FakeDb([{"id": None}, {"id": sid}])
        pipeline = _FakePipeline()

        count = await _advance_pending(db, pipeline)

        assert count == 1
        assert pipeline.advanced == [sid]

    async def test_exception_does_not_abort_remaining(self) -> None:
        sid1, sid2 = uuid4(), uuid4()

        class _PartialFail:
            def __init__(self) -> None:
                self.advanced: list[UUID] = []
                self._call = 0

            async def advance(self, strategy_id: UUID) -> str:
                self._call += 1
                if self._call == 1:
                    raise RuntimeError("first fails")
                self.advanced.append(strategy_id)
                return "shadow"

        db = _FakeDb([{"id": sid1}, {"id": sid2}])
        pipeline = _PartialFail()

        count = await _advance_pending(db, pipeline)

        assert count == 1  # only the second succeeded
        assert pipeline.advanced == [sid2]
