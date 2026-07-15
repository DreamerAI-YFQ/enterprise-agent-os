"""Event bus abstraction for async decoupling.

Business modules publish events; cross-cutting concerns (tracing, RL feedback,
ambient monitors) subscribe. Implementations: in-process (default), Redis
pub/sub (distributed), Kafka (演进).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True)
class Event:
    """A domain event published on the bus."""

    name: str  # e.g. "agent.task.completed"
    tenant_id: UUID
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: UUID = field(default_factory=uuid4)


# Handler type: async callable receiving an Event
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus(Protocol):
    """Event bus protocol. Implementations handle dispatch and persistence."""

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        ...

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """Register an async handler for events matching event_name.

        event_name supports dot-prefix wildcards: "agent.task.*" matches
        "agent.task.completed", "agent.task.failed", etc.
        """
        ...

    async def replay(self, event_name: str, tenant_id: UUID, since: datetime) -> list[Event]:
        """Replay events matching name and tenant since timestamp.

        Used by RL pipeline to reconstruct feedback signals from history.
        """
        ...
