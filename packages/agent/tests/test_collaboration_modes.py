"""Tests for the four collaboration mode executors.

A real async-generator mock runner is used so ``async for`` iteration works
end-to-end. Each test verifies call ordering, output passing, and the final
event content for one mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from eaos.agent.collaboration.modes import (
    DebateExecutor,
    FanOutInExecutor,
    HierarchicalExecutor,
    RelayExecutor,
)
from eaos.agent.orchestrator import (
    CollaborationMode,
    CollaborationPlan,
    SubTask,
)
from eaos.agent.runner import AgentEvent
from eaos.core.context import TenantContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.runner import AgentRunner


class _MockRunner:
    """AgentRunner mock whose ``invoke`` is a real async generator."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str]] = []
        self._responses: dict[UUID, str] = {}

    def set_response(self, agent_id: UUID, content: str) -> None:
        self._responses[agent_id] = content

    async def invoke(
        self, ctx: TenantContext, user_message: str
    ) -> AsyncIterator[AgentEvent]:
        self.calls.append((ctx.agent_id, user_message))
        content = self._responses.get(ctx.agent_id, f"resp-{ctx.agent_id}")
        yield AgentEvent(type="token", content="thinking", agent_id=ctx.agent_id)
        yield AgentEvent(type="final", content=content, agent_id=ctx.agent_id)

    async def interrupt_and_resume(
        self, ctx: TenantContext, approval: dict[str, Any]
    ) -> AsyncIterator[AgentEvent]:
        del approval
        yield AgentEvent(type="error", content="noop", agent_id=ctx.agent_id)


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
        session_id=uuid4(),
    )


def _runner(r: _MockRunner) -> AgentRunner:
    """Cast mock to AgentRunner.

    mypy reads the ``AgentRunner.invoke`` Protocol as a coroutine returning
    AsyncIterator, but ``_MockRunner.invoke`` is a real async generator.
    The two are structurally incompatible to mypy despite matching at runtime.
    """
    return cast("AgentRunner", r)


class TestRelayExecutor:
    async def test_relay_sequential_output_passing(self) -> None:
        agent_a = uuid4()
        agent_b = uuid4()
        agent_c = uuid4()
        runner = _MockRunner()
        runner.set_response(agent_a, "resultA")
        runner.set_response(agent_b, "resultB")
        runner.set_response(agent_c, "resultC")

        s0 = SubTask(description="step0", assigned_agent_id=agent_a)
        s1 = SubTask(
            description="step1", assigned_agent_id=agent_b, depends_on=[s0.task_id]
        )
        s2 = SubTask(
            description="step2", assigned_agent_id=agent_c, depends_on=[s1.task_id]
        )

        plan = CollaborationPlan(
            mode=CollaborationMode.RELAY,
            subtasks=[s0, s1, s2],
        )
        ctx = _ctx()

        events = [e async for e in RelayExecutor().execute(plan, ctx, _runner(runner))]

        called_agents = [c[0] for c in runner.calls]
        assert called_agents == [agent_a, agent_b, agent_c]

        assert "resultA" in runner.calls[1][1]
        assert "resultB" in runner.calls[2][1]

        final_events = [e for e in events if e.type == "final"]
        assert final_events[-1].content == "resultC"

    async def test_relay_empty_subtasks(self) -> None:
        plan = CollaborationPlan(mode=CollaborationMode.RELAY, subtasks=[])
        ctx = _ctx()
        runner = _MockRunner()

        events = [e async for e in RelayExecutor().execute(plan, ctx, _runner(runner))]

        assert len(events) == 1
        assert events[0].type == "final"


class TestFanOutInExecutor:
    async def test_fanout_parallel_then_aggregate(self) -> None:
        agent_a = uuid4()
        agent_b = uuid4()
        aggregator = uuid4()
        runner = _MockRunner()
        runner.set_response(agent_a, "outA")
        runner.set_response(agent_b, "outB")
        runner.set_response(aggregator, "aggregated")

        plan = CollaborationPlan(
            mode=CollaborationMode.FAN_OUT_IN,
            subtasks=[
                SubTask(description="taskA", assigned_agent_id=agent_a),
                SubTask(description="taskB", assigned_agent_id=agent_b),
            ],
            aggregator_agent_id=aggregator,
        )
        ctx = _ctx()

        events = [e async for e in FanOutInExecutor().execute(plan, ctx, _runner(runner))]

        called_agents = {c[0] for c in runner.calls}
        assert agent_a in called_agents
        assert agent_b in called_agents
        assert aggregator in called_agents

        agg_call = next(c for c in runner.calls if c[0] == aggregator)
        assert "outA" in agg_call[1]
        assert "outB" in agg_call[1]

        final_events = [e for e in events if e.type == "final"]
        assert final_events[-1].content == "aggregated"

    async def test_fanout_no_aggregator(self) -> None:
        agent_a = uuid4()
        runner = _MockRunner()
        runner.set_response(agent_a, "outA")

        plan = CollaborationPlan(
            mode=CollaborationMode.FAN_OUT_IN,
            subtasks=[SubTask(description="taskA", assigned_agent_id=agent_a)],
        )
        ctx = _ctx()

        events = [e async for e in FanOutInExecutor().execute(plan, ctx, _runner(runner))]

        final_events = [e for e in events if e.type == "final"]
        assert final_events[-1].content == "outA"


class TestDebateExecutor:
    async def test_debate_multi_perspective_then_judge(self) -> None:
        tech_agent = uuid4()
        finance_agent = uuid4()
        judge = uuid4()
        runner = _MockRunner()
        runner.set_response(tech_agent, "tech view")
        runner.set_response(finance_agent, "finance view")
        runner.set_response(judge, "judgment")

        plan = CollaborationPlan(
            mode=CollaborationMode.DEBATE,
            subtasks=[
                SubTask(
                    description="analyze tech",
                    assigned_agent_id=tech_agent,
                    role="tech_expert",
                ),
                SubTask(
                    description="analyze finance",
                    assigned_agent_id=finance_agent,
                    role="finance_expert",
                ),
            ],
            judge_agent_id=judge,
        )
        ctx = _ctx()

        events = [e async for e in DebateExecutor().execute(plan, ctx, _runner(runner))]

        tech_call = next(c for c in runner.calls if c[0] == tech_agent)
        finance_call = next(c for c in runner.calls if c[0] == finance_agent)
        assert "tech_expert" in tech_call[1]
        assert "finance_expert" in finance_call[1]

        judge_call = next(c for c in runner.calls if c[0] == judge)
        assert "tech view" in judge_call[1]
        assert "finance view" in judge_call[1]

        final_events = [e for e in events if e.type == "final"]
        assert final_events[-1].content == "judgment"


class TestHierarchicalExecutor:
    async def test_hierarchical_delegates_to_aggregator(self) -> None:
        boss = uuid4()
        runner = _MockRunner()
        runner.set_response(boss, "boss decision")

        plan = CollaborationPlan(
            mode=CollaborationMode.HIERARCHICAL,
            subtasks=[SubTask(description="escalate", assigned_agent_id=boss)],
            aggregator_agent_id=boss,
            depth=1,
        )
        ctx = _ctx()

        events = [e async for e in HierarchicalExecutor().execute(plan, ctx, _runner(runner))]

        assert any(c[0] == boss for c in runner.calls)
        final_events = [e for e in events if e.type == "final"]
        assert final_events[-1].content == "boss decision"

    async def test_hierarchical_depth_exceeded(self) -> None:
        plan = CollaborationPlan(
            mode=CollaborationMode.HIERARCHICAL,
            subtasks=[],
            aggregator_agent_id=uuid4(),
            depth=6,
        )
        ctx = _ctx()
        runner = _MockRunner()

        events = [e async for e in HierarchicalExecutor().execute(plan, ctx, _runner(runner))]

        assert len(events) == 1
        assert events[0].type == "error"
        assert "depth" in (events[0].content or "")
        assert len(runner.calls) == 0

    async def test_hierarchical_no_aggregator(self) -> None:
        plan = CollaborationPlan(
            mode=CollaborationMode.HIERARCHICAL,
            subtasks=[],
            depth=1,
        )
        ctx = _ctx()
        runner = _MockRunner()

        events = [e async for e in HierarchicalExecutor().execute(plan, ctx, _runner(runner))]

        assert len(events) == 1
        assert events[0].type == "error"
        assert "aggregator" in (events[0].content or "")
