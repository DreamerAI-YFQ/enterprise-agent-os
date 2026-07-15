"""Tests for PgAmbientMonitor — trigger registration, evaluation, and notification.

DbClient, AgentOrchestrator, and Notifier are mocked. The orchestrator mock
uses a real async generator so ``async for`` works end-to-end.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from eaos.agent.ambient import (
    AmbientTrigger,
    Notifier,
    PgAmbientMonitor,
    TriggerConfig,
)
from eaos.agent.runner import AgentEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.orchestrator import AgentOrchestrator
    from eaos.core.context import TenantContext
    from eaos.infra.db.base import DbClient


class _MockDb:
    """DbClient mock with configurable per-call responses."""

    def __init__(self) -> None:
        self.fetch_responses: deque[list[dict[str, Any]]] = deque()
        self.tenant_fetch_responses: deque[list[dict[str, Any]]] = deque()
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.tenant_fetch_calls: list[tuple[str, UUID, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((sql, params))
        if self.fetch_responses:
            return self.fetch_responses.popleft()
        return []

    async def tenant_scoped_fetch(
        self,
        sql: str,
        tenant_id: UUID,
        *params: Any,
    ) -> list[dict[str, Any]]:
        self.tenant_fetch_calls.append((sql, tenant_id, params))
        if self.tenant_fetch_responses:
            return self.tenant_fetch_responses.popleft()
        return []

    async def execute(self, sql: str, *params: Any) -> None:
        self.execute_calls.append((sql, params))


class _MockOrchestrator:
    """AgentOrchestrator mock whose execute is a real async generator."""

    def __init__(self, *, final_content: str = "orchestrator-final") -> None:
        self.final_content = final_content
        self.execute_calls: list[TenantContext] = []
        self.execute_messages: list[str] = []

    async def execute(
        self,
        ctx: TenantContext,
        user_message: str,
    ) -> AsyncIterator[AgentEvent]:
        self.execute_calls.append(ctx)
        self.execute_messages.append(user_message)
        yield AgentEvent(
            type="final", content=self.final_content, agent_id=ctx.agent_id
        )


class _MockNotifier:
    """Notifier mock recording sent messages."""

    def __init__(self) -> None:
        self.name = "mock"
        self.sent: list[tuple[str, str]] = []

    async def send_message(
        self,
        target: str,
        text: str,
        attachments: list[Any] | None = None,
    ) -> None:
        self.sent.append((target, text))


def _db(d: _MockDb) -> DbClient:
    """Cast mock to DbClient Protocol."""
    return cast("DbClient", d)


def _orchestrator(o: _MockOrchestrator) -> AgentOrchestrator:
    """Cast mock to AgentOrchestrator (async-gen vs coroutine mismatch)."""
    return cast("AgentOrchestrator", o)


def _notifier(n: _MockNotifier) -> Notifier:
    """Cast mock to Notifier Protocol."""
    return cast("Notifier", n)


def _make_monitor(
    *,
    db: _MockDb | None = None,
    orchestrator: _MockOrchestrator | None = None,
    notifier: _MockNotifier | None = None,
) -> tuple[PgAmbientMonitor, _MockDb, _MockOrchestrator, _MockNotifier]:
    db = db or _MockDb()
    orch = orchestrator or _MockOrchestrator()
    notif = notifier or _MockNotifier()
    monitor = PgAmbientMonitor(
        db=_db(db),
        orchestrator=_orchestrator(orch),
        notifier=_notifier(notif),
    )
    return monitor, db, orch, notif


def _threshold_config(
    *,
    agent_id: UUID | None = None,
    sql: str = "SELECT count(*) AS cnt FROM data.inventory",
    op: str = "<",
    value: float = 100,
) -> TriggerConfig:
    return TriggerConfig(
        trigger_type=AmbientTrigger.THRESHOLD,
        agent_id=agent_id or uuid4(),
        condition={"sql": sql, "op": op, "value": value, "description": "low stock"},
        notify_channel="dingtalk",
        interval_sec=300,
    )


def _trigger_row(
    *,
    tenant_id: UUID,
    agent_id: UUID,
    trigger_type: AmbientTrigger = AmbientTrigger.THRESHOLD,
    condition: dict[str, Any] | None = None,
    last_fired_at: datetime | None = None,
    interval_sec: int = 300,
) -> dict[str, Any]:
    if condition is None:
        condition = {
            "sql": "SELECT count(*) AS cnt FROM data.inventory",
            "op": "<",
            "value": 100,
            "description": "low stock",
        }
    return {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "trigger_type": str(trigger_type.value),
        "condition": condition,
        "notify_channel": "dingtalk",
        "interval_sec": interval_sec,
        "last_fired_at": last_fired_at,
    }


class TestRegisterTrigger:
    async def test_register_returns_uuid_and_calls_insert(self) -> None:
        monitor, db, _orch, _notif = _make_monitor()
        tenant_id = uuid4()
        trigger_id = uuid4()
        db.tenant_fetch_responses.append([{"id": trigger_id}])
        config = _threshold_config()

        result = await monitor.register_trigger(tenant_id, config)

        assert result == trigger_id
        assert len(db.tenant_fetch_calls) == 1
        sql, tid, params = db.tenant_fetch_calls[0]
        assert tid == tenant_id
        assert "INSERT INTO agent.triggers" in sql
        assert "RETURNING id" in sql
        # params: agent_id, trigger_type, condition_json, notify_channel, interval_sec
        assert params[0] == config.agent_id
        assert params[1] == "threshold"
        assert params[3] == "dingtalk"
        assert params[4] == 300


class TestUnregisterTrigger:
    async def test_unregister_calls_soft_delete_update(self) -> None:
        monitor, db, _orch, _notif = _make_monitor()
        trigger_id = uuid4()
        tenant_id = uuid4()

        await monitor.unregister_trigger(trigger_id, tenant_id)

        assert len(db.execute_calls) == 1
        sql, params = db.execute_calls[0]
        assert "UPDATE agent.triggers SET enabled = FALSE" in sql
        assert params[0] == trigger_id
        assert params[1] == tenant_id


class TestListTriggers:
    async def test_list_all_triggers_for_tenant(self) -> None:
        monitor, db, _orch, _notif = _make_monitor()
        tenant_id = uuid4()
        agent_id = uuid4()
        db.tenant_fetch_responses.append(
            [_trigger_row(tenant_id=tenant_id, agent_id=agent_id)]
        )

        triggers = await monitor.list_triggers(tenant_id)

        assert len(triggers) == 1
        assert triggers[0].trigger_type == AmbientTrigger.THRESHOLD
        assert triggers[0].agent_id == agent_id
        assert triggers[0].notify_channel == "dingtalk"
        # SQL should filter by tenant_id only (no agent_id WHERE clause)
        sql, _tid, _params = db.tenant_fetch_calls[0]
        assert "agent_id = :p0" not in sql

    async def test_list_triggers_filtered_by_agent(self) -> None:
        monitor, db, _orch, _notif = _make_monitor()
        tenant_id = uuid4()
        agent_id = uuid4()
        db.tenant_fetch_responses.append([])

        await monitor.list_triggers(tenant_id, agent_id=agent_id)

        sql, _tid, params = db.tenant_fetch_calls[0]
        assert "agent_id = :p0" in sql
        assert params[0] == agent_id


class TestThresholdEvaluation:
    async def test_threshold_breach_fires_notification(self) -> None:
        monitor, db, orch, notif = _make_monitor()
        tenant_id = uuid4()
        agent_id = uuid4()
        db.tenant_fetch_responses.append(
            [_trigger_row(tenant_id=tenant_id, agent_id=agent_id)]
        )
        # THRESHOLD eval: fetch returns count=50, threshold value=100, op="<" → fire
        db.fetch_responses.append([{"cnt": 50}])

        await monitor.check_and_notify(tenant_id)

        # orchestrator was called with a proactive trigger message
        assert len(orch.execute_calls) == 1
        assert "主动触发" in orch.execute_messages[0]
        # notifier was called on the trigger's channel
        assert len(notif.sent) == 1
        target, text = notif.sent[0]
        assert target == "dingtalk"
        assert "threshold" in text
        # last_fired_at was updated
        assert len(db.execute_calls) == 1
        update_sql, _params = db.execute_calls[0]
        assert "UPDATE agent.triggers SET last_fired_at" in update_sql

    async def test_threshold_not_breached_no_notification(self) -> None:
        monitor, db, orch, notif = _make_monitor()
        tenant_id = uuid4()
        agent_id = uuid4()
        db.tenant_fetch_responses.append(
            [_trigger_row(tenant_id=tenant_id, agent_id=agent_id)]
        )
        # count=150 > threshold 100 with op="<" → no fire
        db.fetch_responses.append([{"cnt": 150}])

        await monitor.check_and_notify(tenant_id)

        assert len(orch.execute_calls) == 0
        assert len(notif.sent) == 0
        assert len(db.execute_calls) == 0

    async def test_threshold_with_greater_than_op(self) -> None:
        monitor, db, orch, notif = _make_monitor()
        tenant_id = uuid4()
        agent_id = uuid4()
        db.tenant_fetch_responses.append(
            [
                _trigger_row(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    condition={
                        "sql": "SELECT count(*) AS cnt FROM data.alerts",
                        "op": ">",
                        "value": 10,
                        "description": "too many alerts",
                    },
                )
            ]
        )
        # count=25 > 10 → fire
        db.fetch_responses.append([{"cnt": 25}])

        await monitor.check_and_notify(tenant_id)

        assert len(orch.execute_calls) == 1
        assert len(notif.sent) == 1


class TestScheduledTrigger:
    async def test_scheduled_fires_when_due(self) -> None:
        monitor, db, orch, notif = _make_monitor()
        tenant_id = uuid4()
        agent_id = uuid4()
        db.tenant_fetch_responses.append(
            [
                _trigger_row(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    trigger_type=AmbientTrigger.SCHEDULED,
                    condition={"description": "daily report"},
                    last_fired_at=None,
                )
            ]
        )

        await monitor.check_and_notify(tenant_id)

        # SCHEDULED always evaluates True when due; no extra fetch needed
        assert len(orch.execute_calls) == 1
        assert len(notif.sent) == 1
        assert len(db.fetch_calls) == 0


class TestIntervalGuard:
    async def test_recently_fired_trigger_skipped(self) -> None:
        monitor, db, orch, notif = _make_monitor()
        tenant_id = uuid4()
        agent_id = uuid4()
        # fired 10 seconds ago, interval is 300 → not due
        recent = datetime.now(timezone.utc) - timedelta(seconds=10)  # noqa: UP017
        db.tenant_fetch_responses.append(
            [
                _trigger_row(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    trigger_type=AmbientTrigger.SCHEDULED,
                    condition={"description": "daily report"},
                    last_fired_at=recent,
                    interval_sec=300,
                )
            ]
        )

        await monitor.check_and_notify(tenant_id)

        assert len(orch.execute_calls) == 0
        assert len(notif.sent) == 0

    async def test_expired_interval_allows_firing(self) -> None:
        monitor, db, orch, notif = _make_monitor()
        tenant_id = uuid4()
        agent_id = uuid4()
        # fired 600 seconds ago, interval is 300 → due
        old = datetime.now(timezone.utc) - timedelta(seconds=600)  # noqa: UP017
        db.tenant_fetch_responses.append(
            [
                _trigger_row(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    trigger_type=AmbientTrigger.SCHEDULED,
                    condition={"description": "daily report"},
                    last_fired_at=old,
                    interval_sec=300,
                )
            ]
        )

        await monitor.check_and_notify(tenant_id)

        assert len(orch.execute_calls) == 1
        assert len(notif.sent) == 1
