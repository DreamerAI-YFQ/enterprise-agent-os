"""Tests for InMemoryEventBus."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from eaos.core.events import Event
from eaos.core.events_impl import InMemoryEventBus


def _make_event(
    name: str = "agent.task.completed",
    *,
    tenant_id: UUID | None = None,
) -> Event:
    return Event(
        name=name,
        tenant_id=tenant_id or uuid4(),
        payload={"x": 1},
    )


class TestInMemoryEventBusPublish:
    async def test_handler_receives_published_event(self) -> None:
        bus = InMemoryEventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("agent.task.completed", handler)
        event = _make_event()
        await bus.publish(event)

        assert received == [event]

    async def test_wildcard_pattern_matches(self) -> None:
        bus = InMemoryEventBus()
        matched: list[str] = []

        async def handler(event: Event) -> None:
            matched.append(event.name)

        bus.subscribe("agent.task.*", handler)
        await bus.publish(_make_event("agent.task.completed"))
        await bus.publish(_make_event("agent.task.failed"))
        await bus.publish(_make_event("agent.session.started"))

        assert matched == ["agent.task.completed", "agent.task.failed"]

    async def test_multiple_handlers_all_invoked(self) -> None:
        bus = InMemoryEventBus()
        calls: list[str] = []

        async def h1(event: Event) -> None:
            calls.append("h1")

        async def h2(event: Event) -> None:
            calls.append("h2")

        bus.subscribe("e", h1)
        bus.subscribe("e", h2)
        await bus.publish(_make_event("e"))

        assert calls == ["h1", "h2"]

    async def test_handler_exception_does_not_break_others(self) -> None:
        bus = InMemoryEventBus()
        calls: list[str] = []

        async def boom(event: Event) -> None:
            raise RuntimeError("handler crashed")

        async def ok(event: Event) -> None:
            calls.append("ok")

        bus.subscribe("e", boom)
        bus.subscribe("e", ok)
        await bus.publish(_make_event("e"))

        assert calls == ["ok"]


class TestInMemoryEventBusReplay:
    async def test_replay_filters_by_tenant_and_pattern(self) -> None:
        bus = InMemoryEventBus()
        tenant_a = uuid4()
        tenant_b = uuid4()
        old = datetime.now(UTC) - timedelta(hours=1)
        recent = datetime.now(UTC) + timedelta(seconds=1)

        await bus.publish(Event(name="agent.task.completed", tenant_id=tenant_a))
        await bus.publish(Event(name="agent.task.failed", tenant_id=tenant_a))
        await bus.publish(Event(name="agent.task.completed", tenant_id=tenant_b))

        result = await bus.replay("agent.task.*", tenant_a, old)
        assert {e.name for e in result} == {"agent.task.completed", "agent.task.failed"}

        result_recent = await bus.replay("agent.task.*", tenant_a, recent)
        assert result_recent == []

    async def test_replay_unknown_tenant_returns_empty(self) -> None:
        bus = InMemoryEventBus()
        result = await bus.replay("e.*", uuid4(), datetime.now(UTC))
        assert result == []

    async def test_replay_buffer_is_bounded(self) -> None:
        bus = InMemoryEventBus(replay_buffer_size=3)
        tenant = uuid4()
        for _ in range(5):
            await bus.publish(Event(name="e", tenant_id=tenant))

        result = await bus.replay("e", tenant, datetime.now(UTC) - timedelta(hours=1))
        assert len(result) == 3


class TestWaitFor:
    async def test_wait_for_returns_matching_event(self) -> None:
        bus = InMemoryEventBus()

        async def fire() -> None:
            await asyncio.sleep(0.01)
            await bus.publish(_make_event("agent.task.done"))

        asyncio.create_task(fire())
        event = await bus.wait_for("agent.task.done", timeout=1.0)
        assert event.name == "agent.task.done"

    async def test_wait_for_times_out(self) -> None:
        bus = InMemoryEventBus()
        with pytest.raises(asyncio.TimeoutError):
            await bus.wait_for("never", timeout=0.05)
