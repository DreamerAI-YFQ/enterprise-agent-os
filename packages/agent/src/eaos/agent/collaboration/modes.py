"""Collaboration mode executors — four multi-agent coordination patterns.

Each executor implements the same ``execute(plan, ctx, runner)`` signature,
streaming ``AgentEvent`` as sub-tasks run. They are injected into
``AgentOrchestratorImpl`` which dispatches based on ``CollaborationPlan.mode``.

Modes:
    RELAY         — sequential, output of step N feeds step N+1 (topo-sorted)
    FAN_OUT_IN    — parallel sub-tasks, then an aggregator agent merges
    DEBATE        — parallel multi-perspective sub-tasks, then a judge agent
    HIERARCHICAL  — delegate up to a higher-permission agent (depth-limited)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from eaos.agent.runner import AgentEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from eaos.agent.orchestrator import CollaborationPlan, SubTask
    from eaos.agent.runner import AgentRunner
    from eaos.core.context import TenantContext


class CollaborationExecutor(Protocol):
    """Unified interface for the four collaboration mode executors.

    ``execute`` is an async generator: calling it returns an
    ``AsyncIterator`` directly (no ``await``). Declared as a sync method
    returning ``AsyncIterator`` so async generator impls structurally match.
    """

    def execute(
        self,
        plan: CollaborationPlan,
        ctx: TenantContext,
        runner: AgentRunner,
    ) -> AsyncIterator[AgentEvent]:
        ...


def _aiter_invoke(
    runner: AgentRunner,
    ctx: TenantContext,
    message: str,
) -> AsyncIterator[AgentEvent]:
    """Call ``runner.invoke`` and return the async event stream.

    ``AgentRunner.invoke`` is declared as a sync method returning
    ``AsyncIterator`` in the Protocol; concrete impls are async generators,
    so ``invoke()`` returns an AsyncIterator directly (no await needed).
    """
    return runner.invoke(ctx, message)


async def _run_subtask(
    runner: AgentRunner,
    ctx: TenantContext,
    subtask: SubTask,
    *,
    message_prefix: str = "",
) -> tuple[SubTask, list[AgentEvent], str]:
    """Invoke ``runner`` for one subtask and collect its events + final output.

    Derives a child ``TenantContext`` scoped to the subtask's assigned agent.
    If ``message_prefix`` is given (e.g. previous relay output or debate role),
    it is prepended to the subtask description.
    """
    sub_ctx = ctx.for_agent(subtask.assigned_agent_id)
    message = subtask.description
    if message_prefix:
        message = f"{message_prefix}\n\n{message}"

    events: list[AgentEvent] = []
    final_output = ""
    async for event in _aiter_invoke(runner, sub_ctx, message):
        events.append(event)
        if event.type == "final" and event.content:
            final_output = event.content
    return subtask, events, final_output


def _topo_sort(subtasks: list[SubTask]) -> list[SubTask]:
    """Order subtasks by ``depends_on`` (Kahn's algorithm, list-order tiebreak).

    Falls back to original list order if a cycle or missing dependency is
    detected so relay never deadlocks.
    """
    done: set[UUID] = set()
    result: list[SubTask] = []
    remaining = list(subtasks)
    while remaining:
        progress = False
        for s in list(remaining):
            if all(dep in done for dep in s.depends_on):
                result.append(s)
                done.add(s.task_id)
                remaining.remove(s)
                progress = True
        if not progress:
            result.extend(remaining)
            break
    return result


class RelayExecutor:
    """RELAY mode: sequential subtasks, each output feeds the next."""

    async def execute(
        self,
        plan: CollaborationPlan,
        ctx: TenantContext,
        runner: AgentRunner,
    ) -> AsyncIterator[AgentEvent]:
        if not plan.subtasks:
            yield AgentEvent(type="final", content="", agent_id=ctx.agent_id)
            return

        ordered = _topo_sort(plan.subtasks)
        carry_output = ""
        if plan.initial_input:
            carry_output = str(plan.initial_input.get("output", ""))

        last_output = ""
        for subtask in ordered:
            prefix = f"Previous step output:\n{carry_output}" if carry_output else ""
            _sub, events, last_output = await _run_subtask(
                runner, ctx, subtask, message_prefix=prefix
            )
            for event in events:
                yield event
            carry_output = last_output

        yield AgentEvent(
            type="final", content=last_output, agent_id=ctx.agent_id
        )


class FanOutInExecutor:
    """FAN_OUT_IN mode: parallel subtasks, then an aggregator agent merges."""

    async def execute(
        self,
        plan: CollaborationPlan,
        ctx: TenantContext,
        runner: AgentRunner,
    ) -> AsyncIterator[AgentEvent]:
        if not plan.subtasks:
            yield AgentEvent(type="final", content="", agent_id=ctx.agent_id)
            return

        results = await asyncio.gather(
            *(_run_subtask(runner, ctx, s) for s in plan.subtasks)
        )

        for _subtask, events, _output in results:
            for event in events:
                yield event

        if plan.aggregator_agent_id is not None:
            agg_ctx = ctx.for_agent(plan.aggregator_agent_id)
            combined = "\n\n".join(
                f"[{s.description}]: {output}"
                for s, _events, output in results
            )
            prompt = f"请聚合以下子任务结果并给出综合结论:\n{combined}"
            async for event in _aiter_invoke(runner, agg_ctx, prompt):
                yield event
        else:
            last_output = results[-1][2] if results else ""
            yield AgentEvent(
                type="final", content=last_output, agent_id=ctx.agent_id
            )


class DebateExecutor:
    """DEBATE mode: parallel multi-perspective subtasks, then a judge agent."""

    async def execute(
        self,
        plan: CollaborationPlan,
        ctx: TenantContext,
        runner: AgentRunner,
    ) -> AsyncIterator[AgentEvent]:
        if not plan.subtasks:
            yield AgentEvent(type="final", content="", agent_id=ctx.agent_id)
            return

        async def _run_with_role(
            subtask: SubTask,
        ) -> tuple[SubTask, list[AgentEvent], str]:
            role_prefix = ""
            if subtask.role:
                role_prefix = f"[角色: {subtask.role}]"
            return await _run_subtask(
                runner, ctx, subtask, message_prefix=role_prefix
            )

        results = await asyncio.gather(
            *(_run_with_role(s) for s in plan.subtasks)
        )

        for _subtask, events, _output in results:
            for event in events:
                yield event

        if plan.judge_agent_id is not None:
            judge_ctx = ctx.for_agent(plan.judge_agent_id)
            perspectives = "\n\n".join(
                f"[视角 {s.role or s.task_id}]: {output}"
                for s, _events, output in results
            )
            prompt = f"请裁决以下各方观点并给出最终结论:\n{perspectives}"
            async for event in _aiter_invoke(runner, judge_ctx, prompt):
                yield event
        else:
            last_output = results[-1][2] if results else ""
            yield AgentEvent(
                type="final", content=last_output, agent_id=ctx.agent_id
            )


class HierarchicalExecutor:
    """HIERARCHICAL mode: delegate to a higher-permission agent (depth-limited).

    Phase 3 simplification: delegates once to the ``aggregator_agent_id``
    agent. True recursion (agent re-invoking the orchestrator) is handled by
    ``AgentOrchestratorImpl`` in T11; this executor enforces the depth cap.
    """

    _MAX_DEPTH = 5

    async def execute(
        self,
        plan: CollaborationPlan,
        ctx: TenantContext,
        runner: AgentRunner,
    ) -> AsyncIterator[AgentEvent]:
        if plan.depth > self._MAX_DEPTH:
            yield AgentEvent(
                type="error",
                content=f"hierarchical depth {plan.depth} exceeds limit {self._MAX_DEPTH}",
                agent_id=ctx.agent_id,
            )
            return

        if plan.aggregator_agent_id is None:
            yield AgentEvent(
                type="error",
                content="hierarchical mode requires aggregator_agent_id",
                agent_id=ctx.agent_id,
            )
            return

        agg_ctx = ctx.for_agent(plan.aggregator_agent_id)
        message = plan.subtasks[0].description if plan.subtasks else ""
        async for event in _aiter_invoke(runner, agg_ctx, message):
            yield event
