"""T9: Real LLM end-to-end validation.

Validates the full agent runtime link with a real LLM (gpt-4o-mini):
planning → action execution → reflection → final response.

Requires:
- ``EAOS_RUN_LLM=1``  (opt-in; tests are skipped otherwise)
- ``EAOS_RUN_INTEGRATION=1``  (needs live PG)
- ``EAOS_LLM__OPENAI_API_KEY=sk-...``

Run::
    EAOS_RUN_LLM=1 EAOS_RUN_INTEGRATION=1 EAOS_LLM__OPENAI_API_KEY=sk-... \\
        uv run pytest apps/eaos_api/tests/test_real_llm_e2e.py -m llm -v

These tests are NOT run in CI (cost + key leak risk). Run them manually
before release as part of the pre-release checklist.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from eaos.infra.db.base import DbClient
    from eaos.infra.llm.router import LLMRouter

pytestmark = [pytest.mark.integration, pytest.mark.llm]

TID = UUID("00000000-0000-0000-0000-000000000001")
USER_ADMIN = UUID("00000000-0000-0000-0000-000000000201")
AGENT_PERSONAL = UUID("00000000-0000-0000-0000-000000000301")
SESSION_DEMO = UUID("00000000-0000-0000-0000-000000000303")


class _MockEmbedder:
    """Deterministic embedder — avoids needing a real embedding API key."""

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


def _ctx() -> Any:
    from eaos.core.context import TenantContext

    return TenantContext(
        tenant_id=TID,
        user_id=USER_ADMIN,
        agent_id=AGENT_PERSONAL,
        agent_scope="personal",
        session_id=SESSION_DEMO,
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


def _mock_mcp_server() -> Any:
    """Mock MCP server returning a realistic ERP row count."""
    server: Any = MagicMock()
    server.call_tool = AsyncMock(
        return_value={"rows": [{"count": 10}], "sql": "SELECT count(*) FROM erp.customers"}
    )
    server.list_tools = AsyncMock(return_value=[])
    return server


def _make_runner(
    db: DbClient,
    llm: LLMRouter,
    *,
    knowledge_engine: Any | None = None,
    mcp_server: Any | None = None,
) -> Any:
    """Construct LangGraphRunnerImpl with real DB-backed components."""
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


def _event_types(events: list[Any]) -> list[str]:
    return [e.type for e in events]


# -- Fixtures -----------------------------------------------------------------


@pytest.fixture(scope="session")
async def llm_real() -> AsyncGenerator[LLMRouter, None]:
    """Build a real LLMRouter from EAOS_LLM__OPENAI_API_KEY.

    Skips the entire module if no key is set.
    """
    import os

    api_key = os.environ.get("EAOS_LLM__OPENAI_API_KEY")
    if not api_key:
        pytest.skip("EAOS_LLM__OPENAI_API_KEY not set")

    from eaos.core.config import AppConfig
    from eaos_api.wiring import _build_llm

    config = AppConfig.load_config(env_file=None)
    yield _build_llm(config)


# -- Tests --------------------------------------------------------------------


class TestRealLLMDirect:
    """Real LLM drives a direct response (no tools)."""

    async def test_invoke_direct_response(
        self, db: DbClient, llm_real: LLMRouter
    ) -> None:
        runner = _make_runner(db, llm_real)
        ctx = _ctx()

        events = [e async for e in runner.invoke(ctx, "你好，介绍一下你自己")]

        types = _event_types(events)
        assert "final" in types, f"expected 'final' event, got: {types}"
        final = next(e for e in events if e.type == "final")
        assert final.content is not None
        assert len(final.content) > 10, "real LLM response should be non-trivial"


class TestRealLLMRAG:
    """Real LLM + real knowledge engine (mock embedder, real vector store)."""

    async def test_invoke_rag(self, db: DbClient, llm_real: LLMRouter) -> None:
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

        embedder = _MockEmbedder()
        vector_store = PgVectorStore(db)
        memory_store = PgMemoryStore(vector_store, embedder, db)
        ontology_repo = PgOntologyRepository(db)
        rewriter = OntologyQueryRewriter(ontology_repo, llm_real)
        chunker = SemanticChunker(tenant_id=TID)
        retriever = HybridRetriever(vector_store, embedder, db)
        reranker = LLMReranker(llm_real)
        rag = RAGPipelineImpl(chunker, retriever, reranker, embedder, vector_store, db)
        consolidator = SessionMemoryConsolidator(memory_store, llm_real, db)
        knowledge_engine = KnowledgeEngineImpl(
            ontology_repo, rewriter, rag, memory_store, consolidator
        )

        runner = _make_runner(db, llm_real, knowledge_engine=knowledge_engine)
        ctx = _ctx()

        events = [e async for e in runner.invoke(ctx, "ERP操作手册说了什么？")]

        types = _event_types(events)
        assert "final" in types, f"expected 'final' event, got: {types}"


class TestRealLLMText2SQL:
    """Real LLM + real MCP server (ErpConnector against seed ERP data)."""

    async def test_invoke_text2sql(self, db: DbClient, llm_real: LLMRouter) -> None:
        from eaos.data.erp_connector import ErpConnector
        from eaos.data.mcp.server import McpServerImpl

        mcp_server = McpServerImpl({"erp": ErpConnector(db)})

        runner = _make_runner(db, llm_real, mcp_server=mcp_server)
        ctx = _ctx()

        events = [e async for e in runner.invoke(ctx, "查询客户数量")]

        types = _event_types(events)
        assert "final" in types, f"expected 'final' event, got: {types}"
