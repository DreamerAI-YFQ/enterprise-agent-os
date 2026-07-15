"""Multi-agent collaboration orchestrator.

Four modes: relay (sequential), fan-out/in (parallel), debate (multi-perspective),
hierarchical (permission delegation). Single-agent tasks bypass this and run
AgentRunner directly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import UUID, uuid4

from eaos.agent.runner import AgentEvent
from eaos.infra.llm.base import Message

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.collaboration.modes import CollaborationExecutor
    from eaos.agent.dispatcher import AgentDispatcher
    from eaos.agent.runner import AgentRunner
    from eaos.core.context import TenantContext
    from eaos.infra.llm.router import LLMRouter

logger = logging.getLogger(__name__)


class CollaborationMode(StrEnum):
    """Four collaboration modes."""

    SINGLE = "single"  # no collaboration, bypass orchestrator
    RELAY = "relay"  # A -> B -> C sequential
    FAN_OUT_IN = "fan_out_in"  # parallel dispatch + aggregate
    DEBATE = "debate"  # multiple perspectives + judge
    HIERARCHICAL = "hierarchical"  # permission-level delegation


@dataclass(frozen=True)
class SubTask:
    """A sub-task assigned to an agent in collaboration."""

    task_id: UUID = field(default_factory=uuid4)
    description: str = ""
    assigned_agent_id: UUID = UUID(int=0)
    input: dict[str, Any] = field(default_factory=dict)
    depends_on: list[UUID] = field(default_factory=list)  # for relay ordering
    timeout: int = 300
    role: str | None = None  # for debate: "tech_expert", "finance_expert", etc.


@dataclass(frozen=True)
class CollaborationPlan:
    """A collaboration execution plan produced by task analysis."""

    mode: CollaborationMode
    subtasks: list[SubTask] = field(default_factory=list)
    aggregator_agent_id: UUID | None = None  # for fan-out/in
    judge_agent_id: UUID | None = None  # for debate
    initial_input: dict[str, Any] | None = None
    depth: int = 1  # nesting depth (Harness limits to 5)


class AgentOrchestrator(Protocol):
    """Multi-agent collaboration orchestrator.

    Sits above AgentRunner. Analyzes task -> decides mode -> dispatches ->
    monitors -> aggregates. Single-agent tasks pass through to AgentRunner.

    ``execute`` is an async generator: calling it returns an
    ``AsyncIterator`` directly (no ``await``). Declared as a sync method
    returning ``AsyncIterator`` so async generator impls structurally match.
    """

    def execute(
        self,
        ctx: TenantContext,
        user_message: str,
    ) -> AsyncIterator[AgentEvent]:
        """Analyze and execute task, streaming events.

        Single-agent: delegates to AgentRunner directly.
        Multi-agent: orchestrates per CollaborationPlan.
        """
        ...

    async def analyze_task(
        self,
        ctx: TenantContext,
        message: str,
    ) -> CollaborationPlan:
        """Use LLM to determine collaboration mode and sub-task assignment."""
        ...


_ANALYZE_SYSTEM_PROMPT = (
    "你是任务分析专家。分析用户任务，决定协作模式。\n"
    '输出 JSON: {"mode": "single|relay|fan_out_in|debate|hierarchical", '
    '"subtasks": [{"description": "...", "role": "...", "depends_on": []}], '
    '"aggregator_agent_id": null, '
    '"judge_agent_id": null, '
    '"reason": "..."}\n'
    "规则:\n"
    "- 单一领域任务用 single\n"
    "- 跨部门顺序协作用 relay\n"
    "- 并行独立子任务用 fan_out_in\n"
    "- 需要多视角决策用 debate\n"
    "- 需要权限升级用 hierarchical"
)


def _parse_uuid(value: Any) -> UUID | None:
    """Parse a JSON value into a UUID, returning None on failure/null."""
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _parse_plan(content: str) -> CollaborationPlan:
    """Parse LLM JSON output into a CollaborationPlan.

    Falls back to SINGLE mode on any parse error so the orchestrator never
    blocks on a malformed LLM response.
    """
    try:
        parsed = json.loads(content)
        mode = CollaborationMode(str(parsed.get("mode", "single")))
    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        return CollaborationPlan(mode=CollaborationMode.SINGLE)

    subtasks: list[SubTask] = []
    raw_subtasks = parsed.get("subtasks", [])
    if isinstance(raw_subtasks, list):
        for st in raw_subtasks:
            if not isinstance(st, dict):
                continue
            subtasks.append(
                SubTask(
                    description=str(st.get("description", "")),
                    role=st.get("role") if isinstance(st.get("role"), str) else None,
                )
            )

    return CollaborationPlan(
        mode=mode,
        subtasks=subtasks,
        aggregator_agent_id=_parse_uuid(parsed.get("aggregator_agent_id")),
        judge_agent_id=_parse_uuid(parsed.get("judge_agent_id")),
    )


def _aiter(coro: Any) -> AsyncIterator[AgentEvent]:
    """Cast a coroutine/async-gen result to AsyncIterator for ``async for``.

    Protocols declaring ``async def -> AsyncIterator`` are read by mypy as
    coroutines returning AsyncIterator, but concrete impls are async
    generators that return AsyncIterator directly (no await). The cast
    bridges mypy's view; runtime is unchanged.
    """
    return cast("AsyncIterator[AgentEvent]", coro)


class AgentOrchestratorImpl:
    """AgentOrchestrator backed by LLM-driven task analysis.

    Analyzes the user message with an LLM to pick a collaboration mode, then
    dispatches to the matching executor (or the AgentRunner for SINGLE).
    JSON parse failures degrade safely to SINGLE mode.
    """

    def __init__(
        self,
        llm: LLMRouter,
        runner: AgentRunner,
        dispatcher: AgentDispatcher,
        relay_executor: CollaborationExecutor,
        fanout_executor: CollaborationExecutor,
        debate_executor: CollaborationExecutor,
        hierarchical_executor: CollaborationExecutor,
    ) -> None:
        self._llm = llm
        self._runner = runner
        self._dispatcher = dispatcher
        self._executors: dict[CollaborationMode, CollaborationExecutor] = {
            CollaborationMode.RELAY: relay_executor,
            CollaborationMode.FAN_OUT_IN: fanout_executor,
            CollaborationMode.DEBATE: debate_executor,
            CollaborationMode.HIERARCHICAL: hierarchical_executor,
        }

    async def analyze_task(
        self,
        ctx: TenantContext,
        message: str,
    ) -> CollaborationPlan:
        """Use LLM to determine collaboration mode and sub-task assignment."""
        system_prompt = _ANALYZE_SYSTEM_PROMPT
        try:
            agents = await self._dispatcher.list_available(ctx)
            if agents:
                agents_desc = "\n".join(
                    f"- agent_id={a.id}, name={a.name}, scope={a.scope.value}"
                    for a in agents
                )
                system_prompt += f"\n\n可用 Agent 列表:\n{agents_desc}"
        except Exception:
            # Agent listing is best-effort context; analysis works without it.
            logger.debug("agent listing failed during task analysis", exc_info=True)

        llm_messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=message),
        ]
        response = await self._llm.chat(
            llm_messages, task_type="plan", temperature=0.1
        )
        return _parse_plan(response.content)

    async def execute(
        self,
        ctx: TenantContext,
        user_message: str,
    ) -> AsyncIterator[AgentEvent]:
        try:
            plan = await self.analyze_task(ctx, user_message)
        except Exception as exc:
            logger.exception("task analysis failed")
            yield AgentEvent(
                type="error",
                content=str(exc),
                agent_id=ctx.agent_id,
            )
            return

        yield AgentEvent(
            type="plan",
            content=json.dumps(
                {
                    "mode": plan.mode.value,
                    "subtasks": len(plan.subtasks),
                    "aggregator_agent_id": str(plan.aggregator_agent_id)
                    if plan.aggregator_agent_id
                    else None,
                    "judge_agent_id": str(plan.judge_agent_id)
                    if plan.judge_agent_id
                    else None,
                }
            ),
            agent_id=ctx.agent_id,
        )

        if plan.mode == CollaborationMode.SINGLE:
            async for event in _aiter(self._runner.invoke(ctx, user_message)):
                yield event
            return

        executor = self._executors.get(plan.mode)
        if executor is None:
            yield AgentEvent(
                type="error",
                content=f"unsupported collaboration mode: {plan.mode}",
                agent_id=ctx.agent_id,
            )
            return

        try:
            async for event in _aiter(executor.execute(plan, ctx, self._runner)):
                yield event
        except Exception as exc:
            logger.exception("collaboration execution failed")
            yield AgentEvent(
                type="error",
                content=str(exc),
                agent_id=ctx.agent_id,
            )
