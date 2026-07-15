"""Ambient monitor — proactive agent behavior (borrowed from Claude Tag).

Agents don't just respond; they proactively notify on threshold breaches,
stale tasks, new events, and scheduled triggers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import UUID

from eaos.core.context import TenantContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.orchestrator import AgentOrchestrator
    from eaos.agent.runner import AgentEvent
    from eaos.infra.db.base import DbClient

logger = logging.getLogger(__name__)


class AmbientTrigger(StrEnum):
    """Types of proactive triggers."""

    THRESHOLD = "threshold"  # data breach (inventory low)
    STALE_TASK = "stale_task"  # task stalled
    NEW_EVENT = "new_event"  # new customer inquiry
    SCHEDULED = "scheduled"  # daily morning report


@dataclass(frozen=True)
class TriggerConfig:
    """Configuration for an ambient trigger."""

    trigger_type: AmbientTrigger
    agent_id: UUID
    condition: dict[str, Any]  # e.g. {"metric": "inventory", "op": "<", "value": 100}
    notify_channel: str  # slack/dingtalk/web
    interval_sec: int = 300  # check frequency


class AmbientMonitor(Protocol):
    """Background monitor for proactive agent behavior."""

    async def check_and_notify(self, tenant_id: UUID) -> None:
        """Check all triggers for a tenant, fire notifications if conditions met."""
        ...

    async def register_trigger(
        self,
        tenant_id: UUID,
        config: TriggerConfig,
    ) -> UUID:
        """Register a new ambient trigger."""
        ...

    async def unregister_trigger(self, trigger_id: UUID, tenant_id: UUID) -> None:
        """Remove a trigger."""
        ...

    async def list_triggers(
        self,
        tenant_id: UUID,
        agent_id: UUID | None = None,
    ) -> list[TriggerConfig]:
        """List triggers, optionally filtered by agent."""
        ...


class Notifier(Protocol):
    """Minimal notification interface (gateway Channel satisfies this).

    Defined here to avoid a circular runtime dependency between the agent
    and gateway packages; the gateway ``Channel`` protocol is structurally
    compatible.
    """

    name: str

    async def send_message(
        self,
        target: str,
        text: str,
        attachments: list[Any] | None = None,
    ) -> None:
        ...


@dataclass(frozen=True)
class _StoredTrigger:
    """A trigger row loaded from the DB for evaluation."""

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    trigger_type: AmbientTrigger
    condition: dict[str, Any]
    notify_channel: str
    interval_sec: int
    last_fired_at: datetime | None


def _row_to_trigger(row: dict[str, Any]) -> _StoredTrigger:
    """Map a DB row to a _StoredTrigger, parsing JSONB condition."""
    cond_raw = row.get("condition")
    if isinstance(cond_raw, str):
        cond = json.loads(cond_raw)
    elif isinstance(cond_raw, dict):
        cond = cond_raw
    else:
        cond = {}

    last_fired = row.get("last_fired_at")
    if isinstance(last_fired, str):
        last_fired = datetime.fromisoformat(last_fired.replace("Z", "+00:00"))

    return _StoredTrigger(
        id=row["id"],
        tenant_id=row["tenant_id"],
        agent_id=row["agent_id"],
        trigger_type=AmbientTrigger(row["trigger_type"]),
        condition=cond,
        notify_channel=str(row["notify_channel"]),
        interval_sec=int(row.get("interval_sec", 300)),
        last_fired_at=last_fired,
    )


def _aiter(coro: Any) -> AsyncIterator[AgentEvent]:
    """Cast a coroutine/async-gen result to AsyncIterator for ``async for``.

    ``AgentOrchestrator.execute`` is declared ``async def -> AsyncIterator``
    in the Protocol, which mypy reads as a coroutine; concrete impls are
    async generators returning AsyncIterator directly. The cast bridges this.
    """
    return cast("AsyncIterator[AgentEvent]", coro)


class PgAmbientMonitor:
    """AmbientMonitor backed by PostgreSQL + AgentOrchestrator + Notifier.

    Trigger evaluation is intentionally simple (Phase 3): THRESHOLD runs a
    SQL query and compares the scalar; STALE_TASK and NEW_EVENT run canned
    queries; SCHEDULED fires whenever due. ``run_loop`` is a caller-driven
    infinite loop (no internal scheduler) so tests and callers control timing.
    """

    def __init__(
        self,
        db: DbClient,
        orchestrator: AgentOrchestrator,
        notifier: Notifier,
    ) -> None:
        self._db = db
        self._orchestrator = orchestrator
        self._notifier = notifier

    async def register_trigger(
        self,
        tenant_id: UUID,
        config: TriggerConfig,
    ) -> UUID:
        rows = await self._db.tenant_scoped_fetch(
            "INSERT INTO agent.triggers "
            "(tenant_id, agent_id, trigger_type, condition, notify_channel, "
            "interval_sec, enabled) "
            "VALUES (:tenant_id, :p0, :p1, CAST(:p2 AS jsonb), :p3, :p4, TRUE) "
            "RETURNING id",
            tenant_id,
            config.agent_id,
            str(config.trigger_type.value),
            json.dumps(config.condition),
            config.notify_channel,
            config.interval_sec,
        )
        return UUID(str(rows[0]["id"]))

    async def unregister_trigger(
        self, trigger_id: UUID, tenant_id: UUID
    ) -> None:
        await self._db.execute(
            "UPDATE agent.triggers SET enabled = FALSE "
            "WHERE id = :p0 AND tenant_id = :p1",
            trigger_id,
            tenant_id,
        )

    async def list_triggers(
        self,
        tenant_id: UUID,
        agent_id: UUID | None = None,
    ) -> list[TriggerConfig]:
        if agent_id is None:
            rows = await self._db.tenant_scoped_fetch(
                "SELECT id, tenant_id, agent_id, trigger_type, condition, "
                "notify_channel, interval_sec, last_fired_at "
                "FROM agent.triggers WHERE tenant_id = :tenant_id AND enabled",
                tenant_id,
            )
        else:
            rows = await self._db.tenant_scoped_fetch(
                "SELECT id, tenant_id, agent_id, trigger_type, condition, "
                "notify_channel, interval_sec, last_fired_at "
                "FROM agent.triggers "
                "WHERE tenant_id = :tenant_id AND agent_id = :p0 AND enabled",
                tenant_id,
                agent_id,
            )
        return [self._row_to_config(r) for r in rows]

    @staticmethod
    def _row_to_config(row: dict[str, Any]) -> TriggerConfig:
        cond_raw = row.get("condition")
        if isinstance(cond_raw, str):
            condition: dict[str, Any] = json.loads(cond_raw)
        elif isinstance(cond_raw, dict):
            condition = cond_raw
        else:
            condition = {}
        return TriggerConfig(
            trigger_type=AmbientTrigger(row["trigger_type"]),
            agent_id=row["agent_id"],
            condition=condition,
            notify_channel=str(row["notify_channel"]),
            interval_sec=int(row.get("interval_sec", 300)),
        )

    async def check_and_notify(self, tenant_id: UUID) -> None:
        rows = await self._db.tenant_scoped_fetch(
            "SELECT id, tenant_id, agent_id, trigger_type, condition, "
            "notify_channel, interval_sec, last_fired_at "
            "FROM agent.triggers WHERE tenant_id = :tenant_id AND enabled",
            tenant_id,
        )
        now = datetime.now(timezone.utc)  # noqa: UP017
        for row in rows:
            trigger = _row_to_trigger(row)
            if not self._is_due(trigger, now):
                continue
            if not await self._evaluate(trigger):
                continue
            await self._fire(trigger, tenant_id)

    def _is_due(self, trigger: _StoredTrigger, now: datetime) -> bool:
        if trigger.last_fired_at is None:
            return True
        last = trigger.last_fired_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)  # noqa: UP017
        elapsed = (now - last).total_seconds()
        return elapsed >= trigger.interval_sec

    async def _evaluate(self, trigger: _StoredTrigger) -> bool:
        """Evaluate the trigger condition, returning True if it should fire."""
        if trigger.trigger_type == AmbientTrigger.SCHEDULED:
            return True

        if trigger.trigger_type == AmbientTrigger.THRESHOLD:
            return await self._eval_threshold(trigger)

        if trigger.trigger_type == AmbientTrigger.STALE_TASK:
            return await self._eval_stale_task(trigger)

        if trigger.trigger_type == AmbientTrigger.NEW_EVENT:
            return await self._eval_new_event(trigger)

        return False

    async def _eval_threshold(self, trigger: _StoredTrigger) -> bool:
        sql = trigger.condition.get("sql")
        if not isinstance(sql, str) or sql == "":
            return False
        rows = await self._db.fetch(sql)
        if not rows:
            return False
        first_row = rows[0]
        if not first_row:
            return False
        scalar = next(iter(first_row.values()))
        try:
            value = float(scalar)
        except (TypeError, ValueError):
            return False
        threshold = trigger.condition.get("value")
        if not isinstance(threshold, int | float):
            return False
        op = str(trigger.condition.get("op", "<"))
        if op == "<":
            return value < threshold
        if op == "<=":
            return value <= threshold
        if op == ">":
            return value > threshold
        if op == ">=":
            return value >= threshold
        if op == "==":
            return value == threshold
        return False

    async def _eval_stale_task(self, trigger: _StoredTrigger) -> bool:
        stale_after = trigger.condition.get("stale_after_sec", 3600)
        if not isinstance(stale_after, int | float):
            stale_after = 3600
        rows = await self._db.tenant_scoped_fetch(
            "SELECT count(*) AS cnt FROM agent.sessions "
            "WHERE tenant_id = :tenant_id AND status = 'active' "
            "AND last_active_at < now() - (:p0 || ' seconds')::interval",
            trigger.tenant_id,
            str(int(stale_after)),
        )
        count = 0
        if rows and rows[0]:
            try:
                count = int(rows[0].get("cnt", 0))
            except (TypeError, ValueError):
                count = 0
        return count > 0

    async def _eval_new_event(self, trigger: _StoredTrigger) -> bool:
        table = trigger.condition.get("table", "data.query_history")
        if not isinstance(table, str) or table == "":
            table = "data.query_history"
        last_fired = (
            trigger.last_fired_at.isoformat()
            if trigger.last_fired_at is not None
            else "epoch"
        )
        rows = await self._db.fetch(
            f"SELECT count(*) AS cnt FROM {table} "  # noqa: S608
            f"WHERE created_at > '{last_fired}'::timestamptz"
        )
        count = 0
        if rows and rows[0]:
            try:
                count = int(rows[0].get("cnt", 0))
            except (TypeError, ValueError):
                count = 0
        return count > 0

    async def _fire(self, trigger: _StoredTrigger, tenant_id: UUID) -> None:
        """Run the agent for a triggered condition and notify the channel."""
        description = trigger.condition.get("description", trigger.trigger_type.value)
        ctx = TenantContext(
            tenant_id=tenant_id,
            user_id=trigger.agent_id,  # run as the trigger's agent owner
            agent_id=trigger.agent_id,
            agent_scope="company",
        )
        message = f"主动触发: {description}"

        final_output = ""
        try:
            async for event in _aiter(
                self._orchestrator.execute(ctx, message)
            ):
                if event.type == "final" and event.content:
                    final_output = event.content
        except Exception as exc:
            logger.exception("ambient trigger execution failed")
            final_output = f"触发执行失败: {exc}"

        summary = f"[{trigger.trigger_type.value}] {description}\n{final_output}"
        await self._notifier.send_message(trigger.notify_channel, summary)

        await self._db.execute(
            "UPDATE agent.triggers SET last_fired_at = now() "
            "WHERE id = :p0 AND tenant_id = :p1",
            trigger.id,
            tenant_id,
        )

    async def run_loop(
        self,
        tenant_id: UUID,
        interval_sec: int = 60,
    ) -> None:
        """Infinite check loop; caller wraps in ``asyncio.create_task``."""
        while True:
            await self.check_and_notify(tenant_id)
            await asyncio.sleep(interval_sec)
