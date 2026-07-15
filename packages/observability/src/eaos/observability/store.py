"""Trace store protocol and PostgreSQL implementation — persistence for spans.

PgTraceStore writes Span records to the ``trace.spans`` table (HASH-partitioned
by tenant_id). All DB access goes through DbClient with ``:p0, :p1, ...`` named
placeholders.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from eaos.observability.span import Granularity, Span, SpanEvent


class TraceStore(Protocol):
    """Span persistence (PostgreSQL hash-partitioned by tenant_id)."""

    async def start(self, span: Span) -> None:
        """Insert a span at start_time (end_time NULL)."""
        ...

    async def end(self, span: Span) -> None:
        """Update span with end_time, duration, final status, cost."""
        ...

    async def get(self, span_id: UUID, tenant_id: UUID) -> Span:
        """Fetch a span by id."""
        ...

    async def query(
        self,
        tenant_id: UUID,
        filters: dict[str, Any],
        limit: int = 50,
    ) -> list[Span]:
        """Query spans with filters (granularity, agent_id, status, time range)."""
        ...

    async def get_trace(self, trace_id: UUID) -> list[Span]:
        """Fetch all spans in a trace, ordered by start_time (for drill-down)."""
        ...


class TraceDb(Protocol):
    """Minimal DB subset for trace persistence."""

    async def execute(self, sql: str, *params: Any) -> None: ...

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]: ...

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...


def _json_default(obj: Any) -> Any:
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Not JSON serializable: {type(obj)}")


def _to_json(obj: Any) -> str:
    return json.dumps(obj, default=_json_default, ensure_ascii=False)


def _serialize_events(events: list[SpanEvent]) -> str:
    return _to_json(
        [
            {"name": e.name, "timestamp": e.timestamp, "attributes": e.attributes}
            for e in events
        ]
    )


def _parse_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _deserialize_events(value: Any) -> list[SpanEvent]:
    data = _parse_jsonb(value)
    if data is None:
        return []
    events: list[SpanEvent] = []
    for e in data:
        ts = e["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        events.append(
            SpanEvent(
                name=e["name"],
                timestamp=ts,
                attributes=e.get("attributes", {}),
            )
        )
    return events


def _row_to_span(row: dict[str, Any]) -> Span:
    """Convert a DB row dict to a Span dataclass."""
    return Span(
        id=row["id"],
        tenant_id=row["tenant_id"],
        trace_id=row["trace_id"],
        parent_span_id=row.get("parent_span_id"),
        agent_id=row["agent_id"],
        session_id=row.get("session_id"),
        granularity=Granularity(row["granularity"]),
        name=row["name"],
        start_time=row["start_time"],
        end_time=row.get("end_time"),
        duration_ms=row.get("duration_ms"),
        status=row["status"],
        attributes=_parse_jsonb(row.get("attributes") or {}),
        events=_deserialize_events(row.get("events") or []),
        cost_tokens=row.get("cost_tokens"),
        cost_usd=row.get("cost_usd"),
        user_id=row.get("user_id"),
    )


_SELECT_COLUMNS = (
    "id, tenant_id, trace_id, parent_span_id, agent_id, session_id, "
    "granularity, name, start_time, end_time, duration_ms, status, "
    "attributes, events, cost_tokens, cost_usd, user_id"
)


class PgTraceStore:
    """PostgreSQL-backed TraceStore implementing the TraceStore protocol.

    All SQL uses ``:p0, :p1, ...`` named placeholders per DbClient convention.
    """

    def __init__(self, db: TraceDb) -> None:
        self._db = db

    async def start(self, span: Span) -> None:
        """Insert a span row with end_time=NULL, status from span."""
        await self._db.execute(
            """INSERT INTO trace.spans
               (id, tenant_id, trace_id, parent_span_id, agent_id, session_id,
                granularity, name, start_time, end_time, duration_ms, status,
                attributes, events, cost_tokens, cost_usd, user_id)
               VALUES
               (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, NULL, NULL, :p9,
                :p10, :p11, NULL, NULL, :p12)""",
            span.id,
            span.tenant_id,
            span.trace_id,
            span.parent_span_id,
            span.agent_id,
            span.session_id,
            span.granularity.value,
            span.name,
            span.start_time,
            span.status,
            _to_json(span.attributes),
            _serialize_events(span.events),
            span.user_id,
        )

    async def end(self, span: Span) -> None:
        """Update span with end_time, duration_ms, final status, cost, events."""
        await self._db.execute(
            """UPDATE trace.spans
               SET end_time = :p0,
                   duration_ms = :p1,
                   status = :p2,
                   attributes = :p3,
                   events = :p4,
                   cost_tokens = :p5,
                   cost_usd = :p6
               WHERE id = :p7 AND tenant_id = :p8""",
            span.end_time,
            span.duration_ms,
            span.status,
            _to_json(span.attributes),
            _serialize_events(span.events),
            span.cost_tokens,
            span.cost_usd,
            span.id,
            span.tenant_id,
        )

    async def get(self, span_id: UUID, tenant_id: UUID) -> Span:
        """Fetch a single span by id. Raises KeyError if not found."""
        row = await self._db.fetch_one(
            f"SELECT {_SELECT_COLUMNS} FROM trace.spans "
            "WHERE id = :p0 AND tenant_id = :p1",
            span_id,
            tenant_id,
        )
        if row is None:
            raise KeyError(f"Span {span_id} not found for tenant {tenant_id}")
        return _row_to_span(row)

    async def query(
        self,
        tenant_id: UUID,
        filters: dict[str, Any],
        limit: int = 50,
    ) -> list[Span]:
        """Query spans with dynamic filters. Supported keys:
        agent_id, session_id, granularity, status, trace_id, start_time, end_time.
        """
        clauses: list[str] = ["tenant_id = :p0"]
        params: list[Any] = [tenant_id]
        idx = 1

        filter_map: dict[str, str] = {
            "agent_id": "agent_id = :p{idx}",
            "session_id": "session_id = :p{idx}",
            "granularity": "granularity = :p{idx}",
            "status": "status = :p{idx}",
            "trace_id": "trace_id = :p{idx}",
            "start_time": "start_time >= :p{idx}",
            "end_time": "start_time <= :p{idx}",
        }

        for key, value in filters.items():
            template = filter_map.get(key)
            if template is None:
                continue
            if key == "granularity" and isinstance(value, Granularity):
                value = value.value
            clauses.append(template.format(idx=idx))
            params.append(value)
            idx += 1

        clauses.append(f"LIMIT :p{idx}")
        params.append(limit)

        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM trace.spans "
            f"WHERE {' AND '.join(clauses)} ORDER BY start_time DESC"
        )
        rows = await self._db.fetch(sql, *params)
        return [_row_to_span(r) for r in rows]

    async def get_trace(self, trace_id: UUID) -> list[Span]:
        """Fetch all spans in a trace, ordered by start_time."""
        rows = await self._db.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM trace.spans "
            "WHERE trace_id = :p0 ORDER BY start_time ASC",
            trace_id,
        )
        return [_row_to_span(r) for r in rows]
