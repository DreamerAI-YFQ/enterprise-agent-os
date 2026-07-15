"""Tests for AgentOrchestratorImpl — LLM-driven mode dispatch.

LLM responses are mocked to drive each collaboration mode. The runner and
executors are mocked to verify dispatch without running the full graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

from eaos.agent.orchestrator import (
    AgentOrchestratorImpl,
    CollaborationMode,
    CollaborationPlan,
)
from eaos.agent.runner import AgentEvent
from eaos.core.context import TenantContext
from eaos.infra.llm.base import LLMResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.collaboration.modes import CollaborationExecutor
    from eaos.agent.runner import AgentRunner


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
        session_id=uuid4(),
    )


def _llm_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, prompt_tokens=5, completion_tokens=5)


class _MockRunner:
    """AgentRunner mock whose invoke is a real async generator."""

    def __init__(self, *, final_content: str = "runner-final") -> None:
        self.final_content = final_content
        self.invoke_calls: list[str] = []

    async def invoke(
        self, ctx: TenantContext, user_message: str
    ) -> AsyncIterator[AgentEvent]:
        self.invoke_calls.append(user_message)
        yield AgentEvent(type="token", content="thinking", agent_id=ctx.agent_id)
        yield AgentEvent(
            type="final", content=self.final_content, agent_id=ctx.agent_id
        )

    async def interrupt_and_resume(
        self, ctx: TenantContext, approval: dict[str, Any]
    ) -> AsyncIterator[AgentEvent]:
        del approval
        yield AgentEvent(type="error", content="noop", agent_id=ctx.agent_id)


class _MockExecutor:
    """CollaborationExecutor mock recording the plan it received."""

    def __init__(self, *, final_content: str = "executor-final") -> None:
        self.final_content = final_content
        self.received_plans: list[CollaborationPlan] = []

    async def execute(
        self,
        plan: CollaborationPlan,
        ctx: TenantContext,
        runner: AgentRunner,
    ) -> AsyncIterator[AgentEvent]:
        del runner  # not used; mock returns canned events
        self.received_plans.append(plan)
        yield AgentEvent(
            type="final", content=self.final_content, agent_id=ctx.agent_id
        )


def _runner(r: _MockRunner) -> AgentRunner:
    """Cast mock to AgentRunner (mypy async-gen vs coroutine Protocol mismatch)."""
    return cast("AgentRunner", r)


def _executor(e: _MockExecutor) -> CollaborationExecutor:
    """Cast mock to CollaborationExecutor (same Protocol mismatch)."""
    return cast("CollaborationExecutor", e)


def _make_orchestrator(
    *,
    llm_response: LLMResponse | Exception,
    runner: _MockRunner | None = None,
    relay: _MockExecutor | None = None,
    fanout: _MockExecutor | None = None,
    debate: _MockExecutor | None = None,
    hierarchical: _MockExecutor | None = None,
) -> tuple[
    AgentOrchestratorImpl,
    _MockRunner,
    _MockExecutor,
    _MockExecutor,
    _MockExecutor,
    _MockExecutor,
]:
    runner = runner or _MockRunner()
    relay_ex = relay or _MockExecutor(final_content="relay-final")
    fanout_ex = fanout or _MockExecutor(final_content="fanout-final")
    debate_ex = debate or _MockExecutor(final_content="debate-final")
    hier_ex = hierarchical or _MockExecutor(final_content="hier-final")

    llm = AsyncMock()
    if isinstance(llm_response, Exception):
        llm.chat.side_effect = llm_response
    else:
        llm.chat.return_value = llm_response

    dispatcher = AsyncMock()
    dispatcher.list_available.return_value = []

    orch = AgentOrchestratorImpl(
        llm=llm,
        runner=_runner(runner),
        dispatcher=dispatcher,
        relay_executor=_executor(relay_ex),
        fanout_executor=_executor(fanout_ex),
        debate_executor=_executor(debate_ex),
        hierarchical_executor=_executor(hier_ex),
    )
    return orch, runner, relay_ex, fanout_ex, debate_ex, hier_ex


def _event_types(events: list[AgentEvent]) -> list[str]:
    return [e.type for e in events]


class TestSingleMode:
    async def test_single_passthrough(self) -> None:
        orch, runner, relay_ex, _fanout, _debate, _hier = _make_orchestrator(
            llm_response=_llm_response('{"mode": "single"}'),
        )
        ctx = _ctx()

        events = [e async for e in orch.execute(ctx, "hello")]

        types = _event_types(events)
        assert "plan" in types
        assert "final" in types
        final_event = next(e for e in events if e.type == "final")
        assert final_event.content == "runner-final"
        # runner was invoked; no collaboration executor was called
        assert len(runner.invoke_calls) == 1
        assert relay_ex.received_plans == []


class TestRelayDispatch:
    async def test_relay_delegates_to_relay_executor(self) -> None:
        plan_json = (
            '{"mode": "relay", '
            '"subtasks": [{"description": "step1"}, {"description": "step2"}]}'
        )
        orch, runner, relay_ex, _fanout, _debate, _hier = _make_orchestrator(
            llm_response=_llm_response(plan_json),
        )
        ctx = _ctx()

        events = [e async for e in orch.execute(ctx, "cross-dept task")]

        # relay executor received the plan
        assert len(relay_ex.received_plans) == 1
        plan = relay_ex.received_plans[0]
        assert plan.mode == CollaborationMode.RELAY
        assert len(plan.subtasks) == 2

        # runner was NOT invoked directly (relay executor handles subtasks)
        assert runner.invoke_calls == []

        # plan event + relay executor's final event
        types = _event_types(events)
        assert "plan" in types
        assert "final" in types
        final_event = next(e for e in events if e.type == "final")
        assert final_event.content == "relay-final"


class TestFallbackToSingle:
    async def test_invalid_json_degrades_to_single(self) -> None:
        orch, runner, _relay, _fanout, _debate, _hier = _make_orchestrator(
            llm_response=_llm_response("this is not json at all"),
        )
        ctx = _ctx()

        events = [e async for e in orch.execute(ctx, "hi")]

        # degraded to SINGLE → runner invoked
        assert len(runner.invoke_calls) == 1
        types = _event_types(events)
        assert "final" in types

    async def test_invalid_mode_degrades_to_single(self) -> None:
        orch, runner, _relay, _fanout, _debate, _hier = _make_orchestrator(
            llm_response=_llm_response('{"mode": "unknown_mode"}'),
        )
        ctx = _ctx()

        events = [e async for e in orch.execute(ctx, "hi")]

        # invalid mode string → ValueError → fallback to SINGLE
        assert len(runner.invoke_calls) == 1
        assert any(e.type == "final" for e in events)


class TestErrorHandling:
    async def test_llm_error_yields_error_event(self) -> None:
        orch, runner, _relay, _fanout, _debate, _hier = _make_orchestrator(
            llm_response=RuntimeError("llm unavailable"),
        )
        ctx = _ctx()

        events = [e async for e in orch.execute(ctx, "hi")]

        assert len(events) == 1
        assert events[0].type == "error"
        assert "llm unavailable" in (events[0].content or "")
        # runner not invoked because analysis failed before dispatch
        assert runner.invoke_calls == []
