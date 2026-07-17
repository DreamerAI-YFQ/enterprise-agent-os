"""Tests for LangGraphRunnerImpl — Plan-Execute-Reflect graph.

All dependencies are mocked. The graph runs for real, verifying that nodes
wire together correctly and events stream in the expected order.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from eaos.agent.dispatcher import (
    AgentConfig,
    AgentScope,
    CapabilityBoundary,
)
from eaos.agent.runner import AgentEvent, LangGraphRunnerImpl
from eaos.core.context import TenantContext
from eaos.data.mcp.types import McpTool, McpToolResult
from eaos.harness.write_pipeline import WriteApprovalRequired, WriteIntent
from eaos.infra.llm.base import LLMResponse
from eaos.knowledge.engine import SearchResult
from eaos.knowledge.memory.store import Memory, MemoryScope, MemoryType
from eaos.skills.spec import (
    RiskLevel,
    SkillCategory,
    SkillResult,
    SkillScope,
    SkillSpec,
)
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


def _low_risk_skill(
    ctx: TenantContext,
    *,
    name: str = "weekly-summary",
) -> SkillSpec:
    return SkillSpec(
        id=uuid4(),
        tenant_id=ctx.tenant_id,
        scope=SkillScope.PERSONAL,
        owner_id=ctx.user_id,
        name=name,
        display_name="Weekly Summary",
        description="Create a concise weekly summary.",
        category=SkillCategory.DOCUMENT_TEMPLATE,
        risk_level=RiskLevel.LOW,
        instructions="Summarize the supplied period.",
        status="published",
    )


def _history_row(
    ctx: TenantContext,
    *,
    role: str,
    content: str,
    tenant_id: object | None = None,
    session_id: object | None = None,
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "tenant_id": tenant_id or ctx.tenant_id,
        "session_id": session_id or ctx.session_id,
        "role": role,
        "content": content,
        "created_at": "2026-07-17T00:00:00Z",
    }


def _make_runner(
    *,
    llm_responses: list[LLMResponse],
    search_results: list[SearchResult] | None = None,
    skill_output: str = "",
    mcp_result: dict[str, Any] | None = None,
    max_iter: int = 10,
    tool_registry: Any = None,
    db: Any = None,
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
        tool_registry=tool_registry,
        db=db,
    )
    return runner, ctx


def _event_types(events: list[AgentEvent]) -> list[str]:
    return [e.type for e in events]


def _llm_chat_mock(runner: LangGraphRunnerImpl) -> AsyncMock:
    return cast("AsyncMock", runner._llm.chat)


def _resolve_for_user_mock(runner: LangGraphRunnerImpl) -> AsyncMock:
    return cast("AsyncMock", runner._skill_resolver.resolve_for_user)


def _skill_execute_mock(runner: LangGraphRunnerImpl) -> AsyncMock:
    return cast("AsyncMock", runner._skill_executor.execute)


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

    async def test_current_persisted_user_message_is_not_duplicated(self) -> None:
        db = AsyncMock()
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response('{"paradigm":"plan","steps":[{"id":0,"action":"direct"}]}'),
                _llm_response("answer"),
            ],
            db=db,
        )
        db.fetch.return_value = [
            _history_row(ctx, role="user", content="hello"),
        ]

        _ = [event async for event in runner.invoke(ctx, "hello")]
        plan_messages = _llm_chat_mock(runner).call_args_list[0].args[0]
        current_turns = [
            message
            for message in plan_messages
            if message.role == "user" and message.content == "hello"
        ]
        assert len(current_turns) == 1


class TestRagPath:
    async def test_rag_step_returns_results(self) -> None:
        search_results = [
            SearchResult(content="doc-1", score=0.9, source="rag", metadata={}),
            SearchResult(content="doc-2", score=0.8, source="rag", metadata={}),
        ]
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    json.dumps({"steps": [{"id": 0, "action": "rag", "args": {"query": "manual"}}]})
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

    async def test_forced_rag_mode_uses_citation_aware_synthesis(self) -> None:
        search_results = [
            SearchResult(
                content="客户信用额度为 500000 元。",
                score=0.9,
                source="rag",
                metadata={"document_id": "doc-1", "scope": "enterprise"},
            ),
        ]
        runner, original_ctx = _make_runner(
            llm_responses=[_llm_response("客户信用额度为 500000 元。")],
            search_results=search_results,
        )
        ctx = TenantContext(
            tenant_id=original_ctx.tenant_id,
            user_id=original_ctx.user_id,
            agent_id=original_ctx.agent_id,
            agent_scope=original_ctx.agent_scope,
            session_id=original_ctx.session_id,
            mode="rag",
        )

        events = [event async for event in runner.invoke(ctx, "信用额度是多少？")]

        final_event = next(event for event in events if event.type == "final")
        assert final_event.content == "客户信用额度为 500000 元。\n\n来源：[1]"
        assert _llm_chat_mock(runner).await_count == 1

    async def test_forced_rag_mode_does_not_cite_no_evidence_refusal(self) -> None:
        runner, original_ctx = _make_runner(
            llm_responses=[_llm_response("知识库中没有找到相关信息。")],
            search_results=[],
        )
        ctx = TenantContext(
            tenant_id=original_ctx.tenant_id,
            user_id=original_ctx.user_id,
            agent_id=original_ctx.agent_id,
            agent_scope=original_ctx.agent_scope,
            session_id=original_ctx.session_id,
            mode="rag",
        )

        events = [event async for event in runner.invoke(ctx, "不存在的政策？")]

        final_event = next(event for event in events if event.type == "final")
        assert final_event.content == "知识库中没有找到相关信息。"
        assert _llm_chat_mock(runner).await_count == 1


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


class TestDeterministicGovernedMcpPath:
    @pytest.mark.parametrize(
        ("message", "resource", "field", "identifier"),
        [
            ("查询订单 G-ORD-TEST 的完整详情", "orders", "order_no", "G-ORD-TEST"),
            ("查询客户 G-CUS-TEST 的完整资料", "customers", "code", "G-CUS-TEST"),
            ("查询产品 G-PRD-TEST 的全部库存", "products", "sku", "G-PRD-TEST"),
        ],
    )
    async def test_exact_identifier_read_uses_tenant_scoped_tool_without_llm(
        self,
        message: str,
        resource: str,
        field: str,
        identifier: str,
    ) -> None:
        registry = AsyncMock()
        registry.is_write_tool = MagicMock(return_value=False)
        registry.list_tools.return_value = [
            McpTool(
                name="erp_read",
                description="tenant-scoped ERP read",
                input_schema={"type": "object"},
            )
        ]
        registry.call_tool.return_value = McpToolResult(
            content=[{"type": "text", "text": '{"rows":[],"total":0}'}]
        )
        runner, ctx = _make_runner(llm_responses=[], tool_registry=registry)

        events = [event async for event in runner.invoke(ctx, message)]

        assert events[-1].type == "final"
        assert events[-1].content == "未查询到数据，或资源在当前租户不可见。"
        registry.call_tool.assert_awaited_once_with(
            "erp_read",
            {
                "resource": resource,
                "filters": {field: identifier},
                "limit": 10,
            },
            ctx.tenant_id,
        )
        registry.call_write_tool.assert_not_awaited()
        _llm_chat_mock(runner).assert_not_awaited()

    async def test_unsupported_mutation_fails_closed_without_tool_or_llm(self) -> None:
        registry = AsyncMock()
        registry.is_write_tool = MagicMock(return_value=True)
        registry.list_tools.return_value = [
            McpTool(
                name="erp_create_sales_order",
                description="create order",
                input_schema={"type": "object"},
            )
        ]
        runner, ctx = _make_runner(llm_responses=[], tool_registry=registry)

        events = [event async for event in runner.invoke(ctx, "删除订单 ORD-001")]

        assert events[-1].type == "final"
        assert "拒绝执行" in (events[-1].content or "")
        assert "当前没有" in (events[-1].content or "")
        registry.call_tool.assert_not_awaited()
        registry.call_write_tool.assert_not_awaited()
        _llm_chat_mock(runner).assert_not_awaited()

    async def test_batch_change_with_exact_ids_cannot_fall_into_read_shortcut(
        self,
    ) -> None:
        registry = AsyncMock()
        registry.is_write_tool = MagicMock(return_value=False)
        registry.list_tools.return_value = [
            McpTool(
                name="erp_read",
                description="tenant-scoped ERP read",
                input_schema={"type": "object"},
            )
        ]
        runner, ctx = _make_runner(llm_responses=[], tool_registry=registry)

        events = [
            event
            async for event in runner.invoke(
                ctx,
                "把 CUS-001、CUS-002、CUS-003 的账期批量改为 90 天",
            )
        ]

        assert events[-1].type == "final"
        assert "拒绝执行" in (events[-1].content or "")
        registry.call_tool.assert_not_awaited()
        registry.call_write_tool.assert_not_awaited()
        _llm_chat_mock(runner).assert_not_awaited()


class TestReactParadigm:
    async def test_react_direct_done(self) -> None:
        """ReAct: plan→reason(done)→END, no tool calls."""
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(json.dumps({"paradigm": "react", "steps": []})),
                _llm_response(json.dumps({"action": "done", "args": {"answer": "ReAct answer"}})),
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
                _llm_response(json.dumps({"paradigm": "react", "steps": []})),
                _llm_response(json.dumps({"action": "rag", "args": {"query": "test query"}})),
                _llm_response(json.dumps({"action": "done", "args": {"answer": "Based on docs"}})),
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
            + [_llm_response(json.dumps({"done": False, "reason": "continue"}))] * 10,
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
            async for _ in runner.interrupt_and_resume(ctx, {"id": uuid4(), "status": "rejected"}):
                pass

    async def test_pending_approval_yields_error_event(self) -> None:
        runner, ctx = _make_runner(llm_responses=[])

        events = [
            e async for e in runner.interrupt_and_resume(ctx, {"id": uuid4(), "status": "pending"})
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
            e async for e in runner.interrupt_and_resume(ctx, {"id": uuid4(), "status": "approved"})
        ]

        # Should yield a final event (final_output is None since no graph output)
        types = [e.type for e in events]
        assert "final" in types

    async def test_missing_status_treated_as_pending(self) -> None:
        runner, ctx = _make_runner(llm_responses=[])

        events = [e async for e in runner.interrupt_and_resume(ctx, {"id": uuid4()})]

        assert len(events) == 1
        assert events[0].type == "error"

    async def test_write_uses_real_graph_interrupt_and_resumes_once(self) -> None:
        approval_id = uuid4()
        registry = AsyncMock()
        registry.is_write_tool = MagicMock(return_value=True)
        registry.list_tools.return_value = [
            McpTool(
                name="erp_create_sales_order",
                description="create order",
                input_schema={"type": "object"},
            )
        ]

        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    '{"tool_name":"erp_create_sales_order",'
                    '"arguments":{"customer_code":"C001","quantity":1}}'
                ),
                _llm_response('{"done":true,"reason":"write completed"}'),
                _llm_response("created"),
            ],
            tool_registry=registry,
        )
        intent = WriteIntent(
            tenant_id=ctx.tenant_id,
            principal_id=ctx.user_id,
            agent_id=ctx.agent_id,
            tool_name="erp_create_sales_order",
            resource="orders",
            operation="create",
            data={"customer_code": "C001", "quantity": 1},
            session_id=ctx.session_id,
            risk_level="high",
            idempotency_key="idem-1",
        )
        registry.call_write_tool.side_effect = WriteApprovalRequired(approval_id, intent)
        registry.resume_write_tool.return_value = McpToolResult(
            content=[{"type": "text", "text": '{"success":true}'}]
        )

        initial = [event async for event in runner.invoke(ctx, "create order")]
        assert initial[-1].type == "approval_required"
        assert initial[-1].metadata is not None
        assert initial[-1].metadata["approval_id"] == str(approval_id)
        assert all(event.type != "final" for event in initial)

        resumed = [
            event
            async for event in runner.interrupt_and_resume(
                ctx, {"id": str(approval_id), "status": "approved"}
            )
        ]
        assert resumed[-1].type == "final"
        assert resumed[-1].content == "created"
        registry.call_write_tool.assert_awaited_once()
        registry.resume_write_tool.assert_awaited_once()


class TestSkillEndToEnd:
    async def test_low_risk_at_mention_executes_exact_visible_skill(self) -> None:
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response('{"done":true,"reason":"skill completed"}'),
                _llm_response("Weekly summary is ready."),
            ]
        )
        skill = _low_risk_skill(ctx)
        _resolve_for_user_mock(runner).return_value = [skill]
        _skill_execute_mock(runner).return_value = SkillResult(
            success=True,
            output="summary ready",
        )

        events = [
            event
            async for event in runner.invoke(
                ctx,
                "Please run @weekly-summary for this week.",
            )
        ]

        _skill_execute_mock(runner).assert_awaited_once_with(skill, {}, ctx)
        assert _llm_chat_mock(runner).call_args_list[0].kwargs["task_type"] == "reflect"
        assert any(
            event.type == "tool_call"
            and event.metadata is not None
            and event.metadata.get("skill_name") == skill.name
            and event.metadata.get("success") is True
            for event in events
        )
        assert events[-1].type == "final"
        assert events[-1].content == "Weekly summary is ready."

    async def test_planner_auto_selects_low_risk_skill_and_strips_control_data(
        self,
    ) -> None:
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    '{"paradigm":"plan","steps":[{"id":0,"action":"skill",'
                    '"args":{"skill_name":"weekly-summary","period":"week"}}]}'
                ),
                _llm_response('{"done":true,"reason":"skill completed"}'),
                _llm_response("Weekly summary is ready."),
            ]
        )
        skill = _low_risk_skill(ctx)
        _resolve_for_user_mock(runner).return_value = [skill]
        _skill_execute_mock(runner).return_value = SkillResult(
            success=True,
            output="summary ready",
        )

        events = [event async for event in runner.invoke(ctx, "Prepare my weekly summary.")]

        _skill_execute_mock(runner).assert_awaited_once_with(
            skill,
            {"period": "week"},
            ctx,
        )
        assert any(event.type == "plan" for event in events)
        assert events[-1].type == "final"

    @pytest.mark.parametrize(
        "skill_args",
        [
            {},
            {"skill_name": "weekly-summar"},
            {"skill_name": "Weekly-Summary"},
        ],
    )
    async def test_missing_or_near_skill_name_never_falls_back_to_first(
        self,
        skill_args: dict[str, Any],
    ) -> None:
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response(
                    json.dumps(
                        {
                            "paradigm": "plan",
                            "steps": [{"id": 0, "action": "skill", "args": skill_args}],
                        }
                    )
                )
            ]
        )
        _resolve_for_user_mock(runner).return_value = [_low_risk_skill(ctx)]

        events = [event async for event in runner.invoke(ctx, "Prepare a summary.")]

        _skill_execute_mock(runner).assert_not_awaited()
        assert events[-1].type == "final"
        assert "exactly match one visible published Skill" in (events[-1].content or "")

    async def test_invisible_at_mention_fails_closed_without_planner_fallback(
        self,
    ) -> None:
        runner, ctx = _make_runner(llm_responses=[])
        _resolve_for_user_mock(runner).return_value = []

        events = [event async for event in runner.invoke(ctx, "Run @private-payroll now.")]

        _skill_execute_mock(runner).assert_not_awaited()
        _llm_chat_mock(runner).assert_not_awaited()
        assert events[-1].type == "final"
        assert "exactly match one visible published Skill" in (events[-1].content or "")

    async def test_duplicate_visible_skill_name_is_ambiguous_and_fails_closed(
        self,
    ) -> None:
        runner, ctx = _make_runner(llm_responses=[])
        _resolve_for_user_mock(runner).return_value = [
            _low_risk_skill(ctx),
            _low_risk_skill(ctx),
        ]

        events = [event async for event in runner.invoke(ctx, "Run @weekly-summary now.")]

        _skill_execute_mock(runner).assert_not_awaited()
        assert events[-1].type == "final"
        assert "exactly match one visible published Skill" in (events[-1].content or "")

    async def test_executor_failure_is_returned_without_llm_success_synthesis(
        self,
    ) -> None:
        runner, ctx = _make_runner(llm_responses=[])
        skill = _low_risk_skill(ctx)
        _resolve_for_user_mock(runner).return_value = [skill]
        _skill_execute_mock(runner).return_value = SkillResult(
            success=False,
            output="",
            error="required tool dependency is unavailable",
        )

        events = [event async for event in runner.invoke(ctx, "Run @weekly-summary now.")]

        _llm_chat_mock(runner).assert_not_awaited()
        assert events[-1].type == "final"
        assert "failed safely" in (events[-1].content or "")
        assert "required tool dependency is unavailable" in (events[-1].content or "")


class TestConversationHistory:
    async def test_fresh_runner_rehydrates_latest_history_in_model_order(self) -> None:
        db = AsyncMock()
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response('{"steps":[{"id":0,"action":"direct"}]}'),
                _llm_response("It refers to the enterprise plan."),
            ],
            db=db,
        )
        # Newest-first with a timestamp tie: UUID ordering may place the prior
        # assistant row before the just-persisted current user row.
        db.fetch.return_value = [
            _history_row(ctx, role="assistant", content="We chose the enterprise plan."),
            _history_row(ctx, role="user", content="What about it?"),
            _history_row(ctx, role="user", content="Which plan did we choose?"),
        ]

        _ = [event async for event in runner.invoke(ctx, "What about it?")]

        sql_call = db.fetch.call_args
        assert "session_id = :p0 AND tenant_id = :p1" in sql_call.args[0]
        assert "ORDER BY created_at DESC, id DESC LIMIT 21" in sql_call.args[0]
        assert sql_call.args[1:] == (ctx.session_id, ctx.tenant_id)
        plan_messages = _llm_chat_mock(runner).call_args_list[0].args[0]
        dialogue = [
            (message.role, message.content)
            for message in plan_messages
            if message.role in {"user", "assistant"}
        ]
        assert dialogue == [
            ("user", "Which plan did we choose?"),
            ("assistant", "We chose the enterprise plan."),
            ("user", "What about it?"),
        ]

    async def test_history_keeps_latest_twenty_before_current_turn(self) -> None:
        db = AsyncMock()
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response('{"steps":[{"id":0,"action":"direct"}]}'),
                _llm_response("answer"),
            ],
            db=db,
        )
        prior_newest_first = [
            _history_row(ctx, role="user", content=f"turn-{index}") for index in range(25, 0, -1)
        ]
        db.fetch.return_value = [
            _history_row(ctx, role="user", content="current"),
            *prior_newest_first,
        ]

        _ = [event async for event in runner.invoke(ctx, "current")]

        plan_messages = _llm_chat_mock(runner).call_args_list[0].args[0]
        user_contents = [message.content for message in plan_messages if message.role == "user"]
        assert len(user_contents) == 21
        assert user_contents[0] == "turn-6"
        assert user_contents[-2] == "turn-25"
        assert user_contents[-1] == "current"

    async def test_other_tenant_and_session_history_never_reaches_model(self) -> None:
        db = AsyncMock()
        runner, ctx = _make_runner(
            llm_responses=[
                _llm_response('{"steps":[{"id":0,"action":"direct"}]}'),
                _llm_response("answer"),
            ],
            db=db,
        )
        db.fetch.return_value = [
            _history_row(ctx, role="user", content="current"),
            _history_row(
                ctx,
                role="assistant",
                content="OTHER_TENANT_SECRET",
                tenant_id=uuid4(),
            ),
            _history_row(
                ctx,
                role="assistant",
                content="OTHER_SESSION_SECRET",
                session_id=uuid4(),
            ),
            _history_row(ctx, role="assistant", content="scoped answer"),
            _history_row(ctx, role="user", content="scoped question"),
        ]

        _ = [event async for event in runner.invoke(ctx, "current")]

        plan_messages = _llm_chat_mock(runner).call_args_list[0].args[0]
        contents = [str(message.content) for message in plan_messages]
        assert all("OTHER_TENANT_SECRET" not in content for content in contents)
        assert all("OTHER_SESSION_SECRET" not in content for content in contents)
        assert "scoped question" in contents
        assert "scoped answer" in contents
