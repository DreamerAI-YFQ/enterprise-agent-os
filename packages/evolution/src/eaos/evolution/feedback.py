"""Implicit feedback collector — infer user satisfaction from behavior.

Users rarely give explicit ratings; we infer from: adopted suggestion,
used output unchanged, re-asked (changed question), abandoned task, modified
output heavily, explicit thumbs up/down (rare but strong).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4


class SignalType(StrEnum):
    """Types of implicit feedback signals."""

    ADOPTED = "adopted"  # user continued the conversation normally
    USED_UNCHANGED = "used_unchanged"  # user copied output without modification
    REASKED = "reasked"  # user rephrased and asked again
    ABANDONED = "abandoned"  # user left without response
    MODIFIED = "modified"  # user modified > 70% of output
    EXPLICIT = "explicit"  # explicit thumbs up/down


@dataclass(frozen=True)
class FeedbackSignal:
    """A single feedback signal extracted from trace."""

    id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default=UUID(int=0))
    trace_id: UUID = field(default=UUID(int=0))
    span_id: UUID = field(default=UUID(int=0))
    user_id: UUID = field(default=UUID(int=0))
    agent_id: UUID = field(default=UUID(int=0))
    signal_type: SignalType = SignalType.ADOPTED
    signal_value: str = "neutral"  # positive/negative/neutral
    strength: float = 0.5  # 0.0 to 1.0
    captured_at: datetime = field(default_factory=datetime.utcnow)


class FeedbackCollector(Protocol):
    """Implicit feedback collector."""

    async def collect_from_session(
        self,
        session_id: UUID,
    ) -> list[FeedbackSignal]:
        """Scan a session's spans, infer feedback signals."""
        ...

    async def collect_from_trace(
        self,
        trace_id: UUID,
    ) -> list[FeedbackSignal]:
        """Scan a single task trace for feedback."""
        ...

    async def batch_save(
        self,
        signals: list[FeedbackSignal],
    ) -> None:
        """Persist feedback signals."""
        ...

    async def get_signals(
        self,
        tenant_id: UUID,
        date_range: tuple[datetime, datetime] | None = None,
        signal_value: str | None = None,
    ) -> list[FeedbackSignal]:
        """Query historical signals for dataset building."""
        ...


class FeedbackDb(Protocol):
    """Minimal DB subset for feedback persistence and span queries."""

    async def execute(self, sql: str, *params: Any) -> None: ...

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]: ...

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...


_SIMILARITY_THRESHOLD = 0.8

_SIGNAL_STRENGTH: dict[SignalType, float] = {
    SignalType.EXPLICIT: 1.0,
    SignalType.ADOPTED: 0.8,
    SignalType.USED_UNCHANGED: 0.8,
    SignalType.REASKED: 0.6,
    SignalType.ABANDONED: 0.9,
    SignalType.MODIFIED: 0.7,
}

_SPAN_COLUMNS = (
    "id, tenant_id, trace_id, agent_id, session_id, user_id, "
    "name, attributes, start_time"
)


def _parse_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _similarity(a: str, b: str) -> float:
    """Normalized text similarity in [0, 1] for prompt-intent comparison."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _make_signal(
    row: dict[str, Any],
    signal_type: SignalType,
    signal_value: str,
) -> FeedbackSignal:
    """Build a FeedbackSignal from a span row."""
    return FeedbackSignal(
        tenant_id=row["tenant_id"],
        trace_id=row["trace_id"],
        span_id=row["id"],
        user_id=row.get("user_id") or UUID(int=0),
        agent_id=row["agent_id"],
        signal_type=signal_type,
        signal_value=signal_value,
        strength=_SIGNAL_STRENGTH[signal_type],
    )


def _infer_signal(rows: list[dict[str, Any]], idx: int) -> FeedbackSignal:
    """Infer a single feedback signal from a span and its successors.

    Priority: EXPLICIT > MODIFIED > USED_UNCHANGED > ABANDONED > REASKED > ADOPTED.
    """
    current = rows[idx]
    attrs = _parse_jsonb(current.get("attributes") or {})

    fb = attrs.get("feedback")
    if fb:
        val = "positive" if fb == "thumbs_up" else "negative"
        return _make_signal(current, SignalType.EXPLICIT, val)

    modified_ratio = attrs.get("output_modified_ratio", 0)
    if isinstance(modified_ratio, (int, float)) and modified_ratio > 0.7:
        return _make_signal(current, SignalType.MODIFIED, "negative")

    if attrs.get("output_copied"):
        return _make_signal(current, SignalType.USED_UNCHANGED, "positive")

    following = rows[idx + 1 :]
    if not following:
        return _make_signal(current, SignalType.ABANDONED, "negative")

    cur_name = str(current.get("name", ""))
    next_name = str(following[0].get("name", ""))
    if _similarity(cur_name, next_name) > _SIMILARITY_THRESHOLD:
        return _make_signal(current, SignalType.REASKED, "negative")

    return _make_signal(current, SignalType.ADOPTED, "positive")


def _row_to_signal(row: dict[str, Any]) -> FeedbackSignal:
    """Convert a feedback_signals DB row to a FeedbackSignal."""
    return FeedbackSignal(
        id=row["id"],
        tenant_id=row["tenant_id"],
        trace_id=row["trace_id"],
        span_id=row["span_id"],
        user_id=row["user_id"],
        agent_id=row["agent_id"],
        signal_type=SignalType(row["signal_type"]),
        signal_value=row["signal_value"],
        strength=float(row["strength"]),
        captured_at=row["captured_at"],
    )


class FeedbackCollectorImpl:
    """FeedbackCollector backed by trace.spans scans and feedback_signals table.

    Scans TASK-granularity spans (user-visible outputs) and infers implicit
    satisfaction from subsequent behavior. All SQL uses ``:p0, :p1, ...``
    named placeholders per DbClient convention.
    """

    def __init__(self, db: FeedbackDb) -> None:
        self._db = db

    async def collect_from_session(
        self,
        session_id: UUID,
    ) -> list[FeedbackSignal]:
        rows = await self._db.fetch(
            f"SELECT {_SPAN_COLUMNS} FROM trace.spans "
            "WHERE session_id = :p0 AND granularity = 'task' "
            "ORDER BY start_time ASC",
            session_id,
        )
        return [self._infer(rows, i) for i in range(len(rows))]

    async def collect_from_trace(
        self,
        trace_id: UUID,
    ) -> list[FeedbackSignal]:
        rows = await self._db.fetch(
            f"SELECT {_SPAN_COLUMNS} FROM trace.spans "
            "WHERE trace_id = :p0 AND granularity = 'task' "
            "ORDER BY start_time ASC",
            trace_id,
        )
        return [self._infer(rows, i) for i in range(len(rows))]

    async def batch_save(
        self,
        signals: list[FeedbackSignal],
    ) -> None:
        for s in signals:
            await self._db.execute(
                """INSERT INTO evolution.feedback_signals
                   (id, tenant_id, trace_id, span_id, user_id, agent_id,
                    signal_type, signal_value, strength, captured_at)
                   VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9)""",
                s.id,
                s.tenant_id,
                s.trace_id,
                s.span_id,
                s.user_id,
                s.agent_id,
                s.signal_type.value,
                s.signal_value,
                s.strength,
                s.captured_at,
            )

    async def get_signals(
        self,
        tenant_id: UUID,
        date_range: tuple[datetime, datetime] | None = None,
        signal_value: str | None = None,
    ) -> list[FeedbackSignal]:
        clauses: list[str] = ["tenant_id = :p0"]
        params: list[Any] = [tenant_id]
        idx = 1
        if date_range is not None:
            clauses.append(f"captured_at >= :p{idx}")
            params.append(date_range[0])
            idx += 1
            clauses.append(f"captured_at <= :p{idx}")
            params.append(date_range[1])
            idx += 1
        if signal_value is not None:
            clauses.append(f"signal_value = :p{idx}")
            params.append(signal_value)
            idx += 1
        sql = (
            "SELECT id, tenant_id, trace_id, span_id, user_id, agent_id, "
            "signal_type, signal_value, strength, captured_at "
            "FROM evolution.feedback_signals "
            f"WHERE {' AND '.join(clauses)} ORDER BY captured_at DESC"
        )
        rows = await self._db.fetch(sql, *params)
        return [_row_to_signal(r) for r in rows]

    @staticmethod
    def _infer(rows: list[dict[str, Any]], idx: int) -> FeedbackSignal:
        return _infer_signal(rows, idx)
