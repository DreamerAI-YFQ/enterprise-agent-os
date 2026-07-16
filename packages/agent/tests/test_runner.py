"""Tests for LangGraphRunnerImpl — Plan-Execute-Reflect graph.

All dependencies are mocked. The graph runs for real, verifying that nodes
wire together correctly and events stream in the expected order.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from eaos.agent.dispatcher import (
    AgentConfig,
    AgentScope,
    CapabilityBoundary,
)
from eaos.agent.runner import AgentEvent, LangGraphRunnerImpl
from eaos.core.context import TenantContext
from eaos.infra.llm.base import LLMResponse
from eaos.knowledge.engine import SearchResult
from eaos.knowledge.memory.store import Memory, MemoryScope, MemoryType
from langgraph.checkpoint.memory import MemorySaver


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        agent_scope="personal",
        session_id=uuid4(),
    )


def _agent_config(max_iter: int = 10) -> AgentConfig:
    return AgentConfig(
        id=uuid4(),
        tenant_id=uuid4(),
        scope=AgentScope.PERSONAL,
        owner_id=uuid4(),
        name="test-agent",
        description=None,
        model_config={},
        capability=CapabilityBoundary(max_iterations=max_iter),
    )


def _llm_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, prompt_tokens=5, completion_tokens=5)


def _make_runner(
    *,
    llm_responses: list[LLMResponse],
    search_results: list[SearchResult] | None = None,
    skill_output: str = "",
    mcp_result: dict[str, Any] | None = None,
    max_iter: int = 10,
) -> tuple[LangGraphRunnerImpl, TenantContext]:
    ctx = _ctx()
    config = _agent_config(max_iter=max_iter)

    llm = AsyncMock()
    llm.chat.side_effect = llm_responses

    skill_resolver = AsyncMock()
    skill_resolver.resolve_for_agent.return_value = []
    # C13/Fix-8: _understand calls resolve_for_user (not resolve_for_agent);
    # default AsyncMock return value is a non-serializable MagicMock, which
    # breaks LangGraph's MemorySaver checkpointing. Must return a real list.
    skill_resolver.resolve_for_user.return_value = []

    skill_executor = AsyncMock()

    knowledge_engine = AsyncMock()
    knowledge_engine.search.return_value = search_results or []
    knowledge_engine.rewrite_query.return_value = SimpleNamespace(
        rewritten="ontology-rewritten-query"
    )

    mcp_server = AsyncMock()
    mcp_server.call_tool.return_value = mcp_result or {"rows": []}

    memory_engine = AsyncMock()
    memory_engine.recall.return_value = []
    memory_engine.consolidate_session.return_value = []

    tenant_manager = AsyncMock()
    tenant_manager.resolve_thread_id.return_value = "tenant:test:agent:test:session:default"

    dispatcher = AsyncMock()
    dispatcher.get.return_value = config

    runner = LangGraphRunnerImpl(
        llm=llm,
        skill_resolver=skill_resolver,
        skill_executor=skill_executor,
        knowledge_engine=knowledge_engine,
        mcp_server=mcp_server,
        memory_engine=memory_engine,
        tenant_manager=tenant_manager,
        dispatcher=dispatcher,
    )
    return runner, ctx


def _event_types(events: list[AgentEvent]) -> list[str]:
    return [e.type for e in events]


class TestDirectPath:
    async def test_single_direct_step(self) -> None:
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(json.dumps({"steps": [{"id": 0, "action": "direct"}]})),
                _llm_response("Hello! How can I help?"),
            ]
        )

        events = [e async for e in runner.invoke(ctx, "hi")]

        types = _event_types(events)
        assert "plan" in types
        assert "token" in types
        assert "final" in types
        final_event = next(e for e in events if e.type == "final")
        assert final_event.content == "Hello! How can I help?"


class TestRagPath:
    async def test_rag_step_returns_results(self) -> None:
        search_results = [
            SearchResult(content="doc-1", score=0.9, source="rag", metadata={}),
            SearchResult(content="doc-2", score=0.8, source="rag", metadata={}),
        ]
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    json.dumps(
                        {"steps": [{"id": 0, "action": "rag", "args": {"query": "manual"}}]}
                    )
                ),
                # C13/Fix-8: reflect needs an LLM response to decide done
                _llm_response(json.dumps({"done": True, "reason": "sufficient data"})),
                # direct_node synthesizes final answer from RAG observations
                _llm_response("Based on the documents, here is the answer."),
            ],
            search_results=search_results,
        )

        events = [e async for e in runner.invoke(ctx, "query docs")]

        types = _event_types(events)
        assert "plan" in types
        assert "tool_call" in types
        assert "final" in types
        tool_event = next(e for e in events if e.type == "tool_call")
        assert tool_event.content == "rag"
        assert tool_event.metadata is not None
        assert len(tool_event.metadata["results"]) == 2


class TestMcpPath:
    async def test_mcp_step_calls_tool(self) -> None:
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    json.dumps(
                        {"steps": [{"id": 0, "action": "mcp", "args": {"tool_name": "erp_read"}}]}
                    )
                ),
            ],
            mcp_result={"rows": [{"id": 1, "name": "Acme"}], "total": 1},
        )

        events = [e async for e in runner.invoke(ctx, "query erp")]

        types = _event_types(events)
        assert "tool_call" in types
        tool_event = next(e for e in events if e.type == "tool_call")
        assert tool_event.content == "erp_read"

    async def test_mcp_step_rewrites_query_via_ontology(self) -> None:
        """Text2SQL path must invoke ontology rewrite and pass rewritten query to MCP."""
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    json.dumps(
                        {
                            "steps": [
                                {
                                    "id": 0,
                                    "action": "mcp",
                                    "args": {
                                        "tool_name": "erp_read",
                                        "tool_args": {"query": "张三的项目"},
                                    },
                                }
                            ]
                        }
                    )
                ),
            ],
            mcp_result={"rows": [], "total": 0},
        )

        events = [e async for e in runner.invoke(ctx, "查询张三的项目")]

        # ontology rewrite must be called with the original user query
        runner._knowledge_engine.rewrite_query.assert_awaited_once()  # type: ignore[attr-defined]
        called_args = runner._knowledge_engine.rewrite_query.call_args  # type: ignore[attr-defined]
        assert called_args.args[0] == "张三的项目"

        # mcp_server must receive the rewritten query, not the original
        runner._mcp_server.call_tool.assert_awaited_once()  # type: ignore[attr-defined]
        mcp_call = runner._mcp_server.call_tool.call_args  # type: ignore[attr-defined]
        if len(mcp_call.args) > 1:
            passed_tool_args = mcp_call.args[1]
        else:
            passed_tool_args = mcp_call.kwargs.get("tool_args", {})
        assert passed_tool_args["query"] == "ontology-rewritten-query"

        assert any(e.type == "tool_call" for e in events)


class TestReactParadigm:
    async def test_react_direct_done(self) -> None:
        """ReAct: plan→reason(done)→END, no tool calls."""
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    json.dumps({"paradigm": "react", "steps": []})
                ),
                _llm_response(
                    json.dumps(
                        {"action": "done", "args": {"answer": "ReAct answer"}}
                    )
                ),
            ]
        )

        events = [e async for e in runner.invoke(ctx, "hi")]

        types = _event_types(events)
        assert "reason" in types
        assert "final" in types
        final_event = next(e for e in events if e.type == "final")
        assert final_event.content == "ReAct answer"

    async def test_react_with_rag_then_done(self) -> None:
        """ReAct: plan→reason(rag)→rag_node→observe→reason(done)→END."""
        search_results = [
            SearchResult(content="doc-1", score=0.9, source="rag", metadata={}),
        ]
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    json.dumps({"paradigm": "react", "steps": []})
                ),
                _llm_response(
                    json.dumps(
                        {"action": "rag", "args": {"query": "test query"}}
                    )
                ),
                _llm_response(
                    json.dumps(
                        {"action": "done", "args": {"answer": "Based on docs"}}
                    )
                ),
            ],
            search_results=search_results,
        )

        events = [e async for e in runner.invoke(ctx, "search docs")]

        types = _event_types(events)
        assert "reason" in types
        assert "tool_call" in types
        assert "final" in types
        final_event = next(e for e in events if e.type == "final")
        assert final_event.content == "Based on docs"


class TestReflectLoop:
    async def test_reflect_need_more_continues(self) -> None:
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    json.dumps(
                        {
                            "steps": [
                                {"id": 0, "action": "rag"},
                                {"id": 1, "action": "direct"},
                            ]
                        }
                    )
                ),
                _llm_response(json.dumps({"done": False, "reason": "need more"})),
                _llm_response("Final answer after RAG."),
            ],
        )

        events = [e async for e in runner.invoke(ctx, "complex question")]

        types = _event_types(events)
        assert types.count("tool_call") >= 1
        assert "reflect" in types
        assert "final" in types
        final_event = next(e for e in events if e.type == "final")
        assert final_event.content == "Final answer after RAG."

    async def test_iteration_limit_exits(self) -> None:
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    json.dumps(
                        {
                            "steps": [
                                {"id": 0, "action": "rag"},
                                {"id": 1, "action": "rag"},
                                {"id": 2, "action": "rag"},
                            ]
                        }
                    )
                ),
            ]
            + [_llm_response(json.dumps({"done": False, "reason": "continue"}))]
            * 10,
            max_iter=2,
        )

        events = [e async for e in runner.invoke(ctx, "loop question")]

        types = _event_types(events)
        assert "final" in types
        reflect_count = types.count("reflect")
        assert reflect_count <= 3


class TestErrorHandling:
    async def test_error_event_on_exception(self) -> None:
        runner, ctx = _make_runner(
            llm_responses=[_llm_response("not json")],
        )

        events = [e async for e in runner.invoke(ctx, "hi")]

        types = _event_types(events)
        assert "plan" in types
        assert "error" in types
        error_event = next(e for e in events if e.type == "error")
        assert error_event.content is not None

    async def test_dispatcher_error_yields_error_event(self) -> None:
        ctx = _ctx()
        llm = AsyncMock()
        dispatcher = AsyncMock()
        dispatcher.get.side_effect = RuntimeError("agent not found")
        tenant_manager = AsyncMock()

        runner = LangGraphRunnerImpl(
            llm=llm,
            skill_resolver=AsyncMock(),
            skill_executor=AsyncMock(),
            knowledge_engine=AsyncMock(),
            mcp_server=AsyncMock(),
            memory_engine=AsyncMock(),
            tenant_manager=tenant_manager,
            dispatcher=dispatcher,
        )

        events = [e async for e in runner.invoke(ctx, "hi")]

        assert len(events) == 1
        assert events[0].type == "error"
        assert "agent not found" in (events[0].content or "")


class TestMemoryRecall:
    async def test_understand_recalls_memories(self) -> None:
        memory = Memory(
            id=uuid4(),
            tenant_id=uuid4(),
            scope=MemoryScope.PERSONAL,
            owner_id=uuid4(),
            memory_type=MemoryType.FACT,
            content="User prefers concise answers.",
        )
        ctx = _ctx()
        config = _agent_config()

        llm = AsyncMock()
        llm.chat.side_effect = [
            _llm_response(json.dumps({"steps": [{"id": 0, "action": "direct"}]})),
            _llm_response("Brief answer."),
        ]

        memory_engine = AsyncMock()
        memory_engine.recall.return_value = [memory]
        memory_engine.consolidate_session.return_value = []

        skill_resolver = AsyncMock()
        # C13/Fix-8: resolve_for_user must return a serializable list, not MagicMock
        skill_resolver.resolve_for_user.return_value = []

        dispatcher = AsyncMock()
        dispatcher.get.return_value = config
        tenant_manager = AsyncMock()
        tenant_manager.resolve_thread_id.return_value = "t"

        runner = LangGraphRunnerImpl(
            llm=llm,
            skill_resolver=skill_resolver,
            skill_executor=AsyncMock(),
            knowledge_engine=AsyncMock(),
            mcp_server=AsyncMock(),
            memory_engine=memory_engine,
            tenant_manager=tenant_manager,
            dispatcher=dispatcher,
        )

        events = [e async for e in runner.invoke(ctx, "hi")]
        await asyncio.sleep(0)

        memory_engine.recall.assert_awaited_once()
        assert any(e.type == "final" for e in events)
        memory_engine.consolidate_session.assert_awaited_once()


class TestCheckpointerInjection:
    """T1: PostgresSaver replaces MemorySaver for multi-instance checkpoint."""

    def test_default_checkpointer_is_memory_saver(self) -> None:
        """Without explicit checkpointer, runner defaults to MemorySaver."""
        runner = LangGraphRunnerImpl(
            llm=AsyncMock(),
            skill_resolver=AsyncMock(),
            skill_executor=AsyncMock(),
            knowledge_engine=AsyncMock(),
            mcp_server=AsyncMock(),
            memory_engine=AsyncMock(),
            tenant_manager=AsyncMock(),
            dispatcher=AsyncMock(),
        )
        assert isinstance(runner._checkpointer, MemorySaver)

    def test_custom_checkpointer_is_used(self) -> None:
        """An explicitly injected checkpointer is used by the graph."""
        custom = MemorySaver()
        runner = LangGraphRunnerImpl(
            llm=AsyncMock(),
            skill_resolver=AsyncMock(),
            skill_executor=AsyncMock(),
            knowledge_engine=AsyncMock(),
            mcp_server=AsyncMock(),
            memory_engine=AsyncMock(),
            tenant_manager=AsyncMock(),
            dispatcher=AsyncMock(),
            checkpointer=custom,
        )
        assert runner._checkpointer is custom


class TestInterruptAndResume:
    """Tests for HITL interrupt_and_resume — Phase 4 approval gate."""

    async def test_rejected_approval_raises_permission_denied(self) -> None:
        from eaos.core.errors import PermissionDeniedError

        runner, ctx = _make_runner(llm_responses=[])

        with pytest.raises(PermissionDeniedError, match="rejected"):
            async for _ in runner.interrupt_and_resume(
                ctx, {"id": uuid4(), "status": "rejected"}
            ):
                pass

    async def test_pending_approval_yields_error_event(self) -> None:
        runner, ctx = _make_runner(llm_responses=[])

        events = [
            e
            async for e in runner.interrupt_and_resume(
                ctx, {"id": uuid4(), "status": "pending"}
            )
        ]

        assert len(events) == 1
        assert events[0].type == "error"
        assert "pending" in (events[0].content or "")

    async def test_approved_calls_dispatcher_and_tenant_manager(self) -> None:
        runner, ctx = _make_runner(llm_responses=[])

        # Replace graph.astream with a no-op async iterator
        async def _noop_astream(*args: Any, **kwargs: Any) -> Any:
            return
            yield

        runner._graph.astream = _noop_astream

        events = [
            e
            async for e in runner.interrupt_and_resume(
                ctx, {"id": uuid4(), "status": "approved"}
            )
        ]

        # Should yield a final event (final_output is None since no graph output)
        types = [e.type for e in events]
        assert "final" in types

    async def test_missing_status_treated_as_pending(self) -> None:
        runner, ctx = _make_runner(llm_responses=[])

        events = [
            e async for e in runner.interrupt_and_resume(ctx, {"id": uuid4()})
        ]

        assert len(events) == 1
        assert events[0].type == "error"

