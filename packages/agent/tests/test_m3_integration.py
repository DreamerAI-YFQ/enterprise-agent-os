"""M3 integration tests — end-to-end agent runtime validation.

Requires a live PostgreSQL with migrations applied and seed data loaded.
Set ``EAOS_RUN_INTEGRATION=1`` to run.

Covers: single-agent (direct/RAG/Text2SQL/skill), multi-agent collaboration
(relay/fan-out-in/debate), gateway webhook, memory recall + consolidation.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from eaos.agent.orchestrator import AgentOrchestrator
    from eaos.agent.runner import AgentRunner
    from eaos.core.context import TenantContext
    from eaos.infra.db.base import DbClient
    from eaos.infra.llm.router import LLMRouter

pytestmark = pytest.mark.integration

TID = UUID("00000000-0000-0000-0000-000000000001")
USER_ADMIN = UUID("00000000-0000-0000-0000-000000000201")
AGENT_PERSONAL = UUID("00000000-0000-0000-0000-000000000301")
AGENT_DEPARTMENT = UUID("00000000-0000-0000-0000-000000000302")
SESSION_DEMO = UUID("00000000-0000-0000-0000-000000000303")
SKILL_TEXT2SQL = UUID("00000000-0000-0000-0000-000000000401")


class _MockEmbedder:
    """Deterministic embedder for integration tests (no API key needed)."""

    @property
    def dimension(self) -> int:
        return 1024

    @property
    def model_name(self) -> str:
        return "mock-embedder"

    async def embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        return [h[i % 32] / 255.0 for i in range(1024)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


def _llm_resp(content: str) -> Any:
    from eaos.infra.llm.base import LLMResponse

    return LLMResponse(content=content, prompt_tokens=5, completion_tokens=5)


def _mock_llm_router(responses: list[str]) -> Any:
    """Build a mock LLMRouter returning responses in call order."""
    llm: Any = MagicMock()
    llm.chat = AsyncMock(side_effect=[_llm_resp(r) for r in responses])
    return llm


def _ctx(
    *,
    agent_id: UUID = AGENT_PERSONAL,
    user_id: UUID = USER_ADMIN,
    session_id: UUID | None = SESSION_DEMO,
) -> TenantContext:
    from eaos.core.context import TenantContext

    return TenantContext(
        tenant_id=TID,
        user_id=user_id,
        agent_id=agent_id,
        agent_scope="personal",
        session_id=session_id,
    )


def _make_runner(
    db: DbClient,
    llm: LLMRouter,
    *,
    mcp_server: Any | None = None,
    knowledge_engine: Any | None = None,
) -> Any:
    """Construct a full LangGraphRunnerImpl with real DB-backed components."""
    from eaos.agent.dispatcher import PgAgentDispatcher
    from eaos.agent.memory.engine import MemoryEngineImpl
    from eaos.agent.runner import LangGraphRunnerImpl
    from eaos.agent.tenant import PgTenantManager
    from eaos.infra.vector.pgvector_store import PgVectorStore
    from eaos.knowledge.memory.consolidator import SessionMemoryConsolidator
    from eaos.knowledge.memory.store import PgMemoryStore
    from eaos.skills.executor import SkillExecutorImpl
    from eaos.skills.quality import PgSkillQualityMonitor
    from eaos.skills.registry import PgSkillRegistry
    from eaos.skills.resolver import SkillResolverImpl

    embedder = _MockEmbedder()
    vector_store = PgVectorStore(db)
    memory_store = PgMemoryStore(vector_store, embedder, db)
    consolidator = SessionMemoryConsolidator(memory_store, llm, db)
    memory_engine = MemoryEngineImpl(memory_store, consolidator)

    registry = PgSkillRegistry(db)
    monitor = PgSkillQualityMonitor(db, registry)
    skill_resolver = SkillResolverImpl(db)
    skill_executor = SkillExecutorImpl(llm, monitor)

    if knowledge_engine is None:
        knowledge_engine = _mock_knowledge_engine()
    if mcp_server is None:
        mcp_server = _mock_mcp_server()

    dispatcher = PgAgentDispatcher(db)
    tenant_manager = PgTenantManager(db, dispatcher)

    return LangGraphRunnerImpl(
        llm=llm,
        skill_resolver=skill_resolver,
        skill_executor=skill_executor,
        knowledge_engine=knowledge_engine,
        mcp_server=mcp_server,
        memory_engine=memory_engine,
        tenant_manager=tenant_manager,
        dispatcher=dispatcher,
    )


def _mock_knowledge_engine() -> Any:
    """Mock KnowledgeEngine returning empty search results and pass-through rewrite."""
    from eaos.knowledge.ontology.query_rewrite import RewrittenQuery

    engine: Any = MagicMock()
    engine.search = AsyncMock(return_value=[])

    async def _rewrite(query: str, tenant_id: Any) -> RewrittenQuery:
        return RewrittenQuery(
            original=query, rewritten=query, entities=[], ontology_refs=[]
        )

    engine.rewrite_query = _rewrite
    return engine


def _mock_mcp_server(result: str = '{"count": 42}') -> Any:
    """Mock EnterpriseMCPServer returning a canned tool result."""
    server: Any = MagicMock()
    server.call_tool = AsyncMock(return_value=result)
    server.list_tools = AsyncMock(return_value=[])
    return server


def _make_orchestrator(db: DbClient, llm: LLMRouter, runner: Any) -> Any:
    """Construct AgentOrchestratorImpl with 4 collaboration executors."""
    from eaos.agent.collaboration.modes import (
        CollaborationExecutor,
        DebateExecutor,
        FanOutInExecutor,
        HierarchicalExecutor,
        RelayExecutor,
    )
    from eaos.agent.dispatcher import PgAgentDispatcher
    from eaos.agent.orchestrator import AgentOrchestratorImpl

    dispatcher = PgAgentDispatcher(db)
    return AgentOrchestratorImpl(
        llm=llm,
        runner=cast("AgentRunner", runner),
        dispatcher=dispatcher,
        relay_executor=cast("CollaborationExecutor", RelayExecutor()),
        fanout_executor=cast("CollaborationExecutor", FanOutInExecutor()),
        debate_executor=cast("CollaborationExecutor", DebateExecutor()),
        hierarchical_executor=cast("CollaborationExecutor", HierarchicalExecutor()),
    )


def _aiter(coro: Any) -> Any:
    """Cast coroutine/async-gen to async iterable for ``async for``."""
    return coro


def _runner(r: Any) -> AgentRunner:
    return cast("AgentRunner", r)


def _orchestrator(o: Any) -> AgentOrchestrator:
    return cast("AgentOrchestrator", o)


def _event_types(events: list[Any]) -> list[str]:
    return [e.type for e in events]


# -- Single-Agent Tests --------------------------------------------------


class TestSingleAgentDirect:
    async def test_direct_response(self, db: DbClient) -> None:
        llm = _mock_llm_router([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "你好！我是企业助手，有什么可以帮助你的？",
            '{"done": true, "reason": "greeting answered"}',
        ])
        runner = _make_runner(db, llm)
        ctx = _ctx()

        events = [e async for e in runner.invoke(ctx, "你好")]

        types = _event_types(events)
        assert "final" in types
        final = next(e for e in events if e.type == "final")
        assert final.content is not None
        assert "企业助手" in final.content or "你好" in final.content


class TestSingleAgentRAG:
    async def test_rag_query(self, db: DbClient) -> None:
        from eaos.infra.vector.pgvector_store import PgVectorStore
        from eaos.knowledge.engine import KnowledgeEngineImpl
        from eaos.knowledge.memory.consolidator import SessionMemoryConsolidator
        from eaos.knowledge.memory.store import PgMemoryStore
        from eaos.knowledge.ontology.query_rewrite import OntologyQueryRewriter
        from eaos.knowledge.ontology.repository import PgOntologyRepository
        from eaos.knowledge.rag.chunker import SemanticChunker
        from eaos.knowledge.rag.pipeline import RAGPipelineImpl
        from eaos.knowledge.rag.reranker import LLMReranker
        from eaos.knowledge.rag.retriever import HybridRetriever

        llm = _mock_llm_router([
            '{"rewritten": "ERP操作手册", "entities": [], "notes": ""}',
            '{"ranked": [0]}',
            '{"steps": [{"id": 0, "action": "rag", "args": {"query": "ERP操作手册"}}]}',
            "根据ERP操作手册，系统支持创建采购订单和审核订单。",
            '{"done": true, "reason": "rag result delivered"}',
        ])

        embedder = _MockEmbedder()
        vector_store = PgVectorStore(db)
        memory_store = PgMemoryStore(vector_store, embedder, db)
        ontology_repo = PgOntologyRepository(db)
        rewriter = OntologyQueryRewriter(ontology_repo, llm)
        chunker = SemanticChunker(tenant_id=TID)
        retriever = HybridRetriever(vector_store, embedder, db)
        reranker = LLMReranker(llm)
        rag = RAGPipelineImpl(chunker, retriever, reranker, embedder, vector_store, db)
        consolidator = SessionMemoryConsolidator(memory_store, llm, db)
        knowledge_engine = KnowledgeEngineImpl(
            ontology_repo, rewriter, rag, memory_store, consolidator
        )

        runner = _make_runner(db, llm, knowledge_engine=knowledge_engine)
        ctx = _ctx()

        events = [e async for e in runner.invoke(ctx, "ERP操作手册说了什么？")]

        types = _event_types(events)
        assert "final" in types


class TestSingleAgentText2SQL:
    async def test_text2sql_via_mcp(self, db: DbClient) -> None:
        mcp = _mock_mcp_server(
            result='{"rows": [{"count": 10}], "sql": "SELECT count(*) FROM erp.customers"}'
        )
        llm = _mock_llm_router([
            '{"steps": [{"id": 0, "action": "mcp", '
            '"args": {"tool_name": "erp_read", "tool_args": {}}}]}',
            "查询结果显示共有10个客户。",
            '{"done": true, "reason": "sql result delivered"}',
        ])
        runner = _make_runner(db, llm, mcp_server=mcp)
        ctx = _ctx()

        events = [e async for e in runner.invoke(ctx, "查询客户数量")]

        types = _event_types(events)
        assert "final" in types
        mcp.call_tool.assert_awaited()


class TestSingleAgentSkill:
    async def test_skill_execution(self, db: DbClient) -> None:
        llm = _mock_llm_router([
            '{"steps": [{"id": 0, "action": "skill", "args": {"skill_name": "text2sql"}}]}',
            "Skill executed: SELECT 1",
            '{"done": true, "reason": "skill completed"}',
        ])
        runner = _make_runner(db, llm)
        ctx = _ctx()

        events = [e async for e in runner.invoke(ctx, "执行text2sql skill")]

        types = _event_types(events)
        assert "final" in types


# -- Multi-Agent Collaboration Tests -------------------------------------


class TestRelayCollaboration:
    async def test_relay_sequential(self, db: DbClient) -> None:
        plan_json = (
            '{"mode": "relay", '
            '"subtasks": [{"description": "step1"}, {"description": "step2"}]}'
        )
        llm = _mock_llm_router([plan_json])
        runner = _make_runner(db, _mock_llm_router([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "step1 output",
            '{"done": true, "reason": ""}',
        ]))
        orchestrator = _make_orchestrator(db, llm, runner)
        ctx = _ctx()

        events = [e async for e in _aiter(_orchestrator(orchestrator).execute(ctx, "跨部门任务"))]

        types = _event_types(events)
        assert "plan" in types
        assert "final" in types


class TestFanOutInCollaboration:
    async def test_fan_out_in_parallel(self, db: DbClient) -> None:
        plan_json = (
            '{"mode": "fan_out_in", '
            '"subtasks": [{"description": "task-a"}, {"description": "task-b"}], '
            f'"aggregator_agent_id": "{AGENT_DEPARTMENT}"}}'
        )
        llm = _mock_llm_router([plan_json])
        runner = _make_runner(db, _mock_llm_router([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "aggregated result",
            '{"done": true, "reason": ""}',
        ]))
        orchestrator = _make_orchestrator(db, llm, runner)
        ctx = _ctx()

        events = [e async for e in _aiter(_orchestrator(orchestrator).execute(ctx, "并行任务"))]

        types = _event_types(events)
        assert "plan" in types


class TestDebateCollaboration:
    async def test_debate_multi_perspective(self, db: DbClient) -> None:
        plan_json = (
            '{"mode": "debate", '
            '"subtasks": [{"description": "tech view", "role": "tech"}, '
            '{"description": "finance view", "role": "finance"}], '
            f'"judge_agent_id": "{AGENT_DEPARTMENT}"}}'
        )
        llm = _mock_llm_router([plan_json])
        runner = _make_runner(db, _mock_llm_router([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "judge verdict",
            '{"done": true, "reason": ""}',
        ]))
        orchestrator = _make_orchestrator(db, llm, runner)
        ctx = _ctx()

        events = [e async for e in _aiter(_orchestrator(orchestrator).execute(ctx, "决策任务"))]

        types = _event_types(events)
        assert "plan" in types


# -- Gateway End-to-End Test ----------------------------------------------


class TestGatewayWebhook:
    async def test_webhook_to_response(self, db: DbClient) -> None:
        from eaos.gateway.im.gateway import MessageGatewayImpl
        from eaos.gateway.im.message import UnifiedMessage

        llm = _mock_llm_router([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "gateway response",
            '{"done": true, "reason": ""}',
        ])
        runner = _make_runner(db, llm)
        orchestrator = _make_orchestrator(db, llm, runner)

        received_events: list[Any] = []

        class _MockChannel:
            name = "mock"

            async def verify_signature(self, raw: bytes, signature: str) -> bool:
                return True

            async def parse_webhook(
                self, raw: dict[str, Any], headers: dict[str, str]
            ) -> UnifiedMessage:
                return UnifiedMessage(
                    channel="mock",
                    channel_message_id="webhook-001",
                    tenant_id=TID,
                    user_id=USER_ADMIN,
                    user_name="tester",
                    agent_id=AGENT_PERSONAL,
                    text="hello via webhook",
                )

            async def send_streaming(
                self, target: str, event_stream: AsyncIterator[Any]
            ) -> None:
                async for event in event_stream:
                    received_events.append(event)

            async def send_message(
                self, target: str, text: str, attachments: list[Any] | None = None
            ) -> None:
                pass

        gw = MessageGatewayImpl(orchestrator=_orchestrator(orchestrator))
        gw.register_channel(cast("Any", _MockChannel()))

        import asyncio

        response = await gw.handle_webhook(
            "mock", {"text": "hello"}, {"signature": "sig"}
        )
        await asyncio.sleep(0.1)

        assert response["status"] == "accepted"
        assert response["message_id"] == "webhook-001"
        assert len(received_events) > 0
        assert any(e.type == "final" for e in received_events)


# -- Memory Tests --------------------------------------------------------


class TestMemoryRecall:
    async def test_recall_in_understand(self, db: DbClient) -> None:
        from eaos.agent.memory.engine import MemoryEngineImpl
        from eaos.infra.vector.pgvector_store import PgVectorStore
        from eaos.knowledge.memory.consolidator import SessionMemoryConsolidator
        from eaos.knowledge.memory.store import MemoryScope, PgMemoryStore

        embedder = _MockEmbedder()
        vector_store = PgVectorStore(db)
        memory_store = PgMemoryStore(vector_store, embedder, db)
        consolidator = SessionMemoryConsolidator(memory_store, _mock_llm_router(["{}"]), db)
        memory_engine = MemoryEngineImpl(memory_store, consolidator)

        await memory_engine.store(
            content="用户偏好中文回复",
            tenant_id=TID,
            scope=MemoryScope.PERSONAL,
            owner_id=USER_ADMIN,
            memory_type="preference",
            source="test",
        )

        llm = _mock_llm_router([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "好的，我会用中文回复。",
            '{"done": true, "reason": "answered"}',
        ])
        runner = _make_runner(db, llm)
        ctx = _ctx()

        events = [e async for e in runner.invoke(ctx, "你好")]

        types = _event_types(events)
        assert "final" in types


class TestSessionConsolidation:
    async def test_consolidation_after_invoke(self, db: DbClient) -> None:
        import asyncio

        llm = _mock_llm_router([
            '{"steps": [{"id": 0, "action": "direct", "args": {}}]}',
            "session content for consolidation",
            '{"done": true, "reason": "done"}',
            '{"memories": ["用户询问了session测试"]}',
        ])
        runner = _make_runner(db, llm)
        ctx = _ctx(session_id=SESSION_DEMO)

        events = [e async for e in runner.invoke(ctx, "session测试")]
        await asyncio.sleep(0.2)

        types = _event_types(events)
        assert "final" in types
