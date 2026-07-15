"""In-process EventBus implementation.

Default for local/dev. Production swaps in a Redis-pub/sub or Kafka backed
implementation behind the same EventBus protocol (see events.py).

Design:
- subscribe() supports dot-prefix wildcards via fnmatch ("agent.task.*").
- publish() awaits all matching handlers sequentially; one handler raising
  does NOT abort the others (errors are swallowed + logged). Sequential keeps
  the call stack simple for a prototype; a concurrent variant can come later.
- replay() serves the RL pipeline's need to reconstruct history. We retain a
  bounded ring buffer per tenant (10000 events) — sufficient for dev/demo;
  production persists events to PostgreSQL before replaying.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections import defaultdict, deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime
    from uuid import UUID

    from eaos.core.events import Event, EventHandler

_logger = logging.getLogger(__name__)

_REPLAY_BUFFER_PER_TENANT = 10_000


class InMemoryEventBus:
    """Reference EventBus implementation backed by in-process state.

    Thread-safety: subscribe() is intended to run at app bootstrap before
    publishers fire; concurrent subscribe during publish is not protected.
    publish()/replay() are safe under asyncio single-threaded execution.
    """

    def __init__(self, *, replay_buffer_size: int = _REPLAY_BUFFER_PER_TENANT) -> None:
        self._handlers: list[tuple[str, EventHandler]] = []
        self._history: dict[UUID, deque[Event]] = defaultdict(
            lambda: deque(maxlen=replay_buffer_size)
        )

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """Register an async handler. event_name supports fnmatch wildcards."""
        self._handlers.append((event_name, handler))

    async def publish(self, event: Event) -> None:
        """Dispatch event to all matching handlers; record in per-tenant history.

        Handler exceptions are caught and logged so one bad subscriber cannot
        break the dispatch chain.
        """
        self._history[event.tenant_id].append(event)
        for pattern, handler in self._handlers:
            if not fnmatch.fnmatch(event.name, pattern):
                continue
            try:
                await handler(event)
            except Exception:
                _logger.exception(
                    "event handler failed",
                    extra={"event_name": event.name, "event_id": str(event.event_id)},
                )

    async def replay(
        self, event_name: str, tenant_id: UUID, since: datetime
    ) -> list[Event]:
        """Return events matching fnmatch pattern, tenant, and >= since timestamp."""
        events: Iterable[Event] = self._history.get(tenant_id, deque())
        # Materialize first (deque could mutate if a concurrent publish ran),
        # then filter by pattern + timestamp.
        return [
            e
            for e in list(events)
            if e.timestamp >= since and fnmatch.fnmatch(e.name, event_name)
        ]

    async def wait_for(
        self,
        event_name: str,
        *,
        timeout: float = 5.0,
    ) -> Event:
        """Block until an event matching event_name is published. Test helper.

        Not part of the EventBus protocol; only use in tests.
        """
        future: asyncio.Future[Event] = asyncio.get_event_loop().create_future()

        async def _match(event: Event) -> None:
            if fnmatch.fnmatch(event.name, event_name) and not future.done():
                future.set_result(event)

        self.subscribe(event_name, _match)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._handlers = [
                (p, h) for (p, h) in self._handlers if h is not _match
            ]
