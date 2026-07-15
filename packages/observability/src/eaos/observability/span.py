"""Span model — four-granularity trace spans.

Granularities: call (LLM call), tool (skill/tool invocation), task (full
agent task with plan+reflect), session (cross-interaction continuity).
All spans share trace_id within a single task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class Granularity(StrEnum):
    """Four trace granularities."""

    CALL = "call"  # single LLM call
    TOOL = "tool"  # single skill/tool invocation
    TASK = "task"  # full agent task (plan -> execute -> reflect)
    SESSION = "session"  # cross-interaction session


@dataclass
class SpanEvent:
    """An event within a span (e.g. tool_call_made, reflection_started)."""

    name: str
    timestamp: datetime
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Span:
    """A single trace span."""

    id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default=UUID(int=0))
    trace_id: UUID = field(default_factory=uuid4)  # shared within a task
    parent_span_id: UUID | None = None
    agent_id: UUID = field(default=UUID(int=0))
    session_id: UUID | None = None
    user_id: UUID | None = None
    granularity: Granularity = Granularity.CALL
    name: str = ""
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: datetime | None = None
    duration_ms: int | None = None
    status: str = "ok"  # ok/error/timeout
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)
    cost_tokens: int | None = None
    cost_usd: float | None = None
