"""M2 integration tests for the knowledge package.

Requires a live PostgreSQL with migrations applied and seed data loaded.
Set ``EAOS_RUN_INTEGRATION=1`` to run.

Covers: ontology schema mapping, query rewrite, RAG ingest/retrieve,
memory store/recall, memory consolidation.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

pytestmark = pytest.mark.integration

TID = UUID("00000000-0000-0000-0000-000000000001")
DS_ERP = UUID("00000000-0000-0000-0000-000000000509")
SESSION_DEMO = UUID("00000000-0000-0000-0000-000000000303")


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


def _mock_llm(response_content: str) -> Any:
    """Build a mock LLMRouter returning a fixed response."""
    from eaos.infra.llm.base import LLMResponse

    llm: Any = MagicMock()
    llm.chat = AsyncMock(return_value=LLMResponse(content=response_content))
    return llm


class TestOntology:
    async def test_get_schema_mapping(self, db: DbClient) -> None:
        from eaos.knowledge.ontology.repository import PgOntologyRepository

        repo = PgOntologyRepository(db)
        mapping = await repo.get_schema_mapping(TID, DS_ERP)

        assert "erp.customers" in mapping
        customer_cols = mapping["erp.customers"]
        assert "name" in customer_cols
        assert customer_cols["name"]["chinese_name"] == "客户名称"

    async def test_search_nodes(self, db: DbClient) -> None:
        from eaos.knowledge.ontology.repository import PgOntologyRepository

        repo = PgOntologyRepository(db)
        nodes = await repo.search_nodes(TID, "客户", top_k=5)

        assert len(nodes) > 0
        assert any("客户" in n.name or "客户" in str(n.properties) for n in nodes)

    async def test_query_rewrite(self, db: DbClient) -> None:
        from eaos.knowledge.ontology.query_rewrite import OntologyQueryRewriter

        llm_response = (
            '{"rewritten": "客户张三的订单", '
            '"entities": [{"name": "张三", "type": "customer"}], '
            '"notes": "消歧客户名称"}'
        )
        llm = _mock_llm(llm_response)
        from eaos.knowledge.ontology.repository import PgOntologyRepository

        rewriter = OntologyQueryRewriter(PgOntologyRepository(db), llm)
        result = await rewriter.rewrite("张三的订单", TID)

        assert result.rewritten == "客户张三的订单"
        assert len(result.entities) == 1
        assert result.entities[0]["name"] == "张三"


class TestRAG:
    async def test_rag_ingest_and_retrieve(self, db: DbClient) -> None:
        from eaos.infra.vector.pgvector_store import PgVectorStore
        from eaos.knowledge.rag.chunker import SemanticChunker
        from eaos.knowledge.rag.pipeline import Document, RAGPipelineImpl
        from eaos.knowledge.rag.reranker import LLMReranker
        from eaos.knowledge.rag.retriever import HybridRetriever

        embedder = _MockEmbedder()
        vector_store = PgVectorStore(db)
        chunker = SemanticChunker(tenant_id=TID)
        retriever = HybridRetriever(vector_store, embedder, db)
        reranker_llm = _mock_llm('{"ranked": [0]}')
        reranker = LLMReranker(reranker_llm)
        pipeline = RAGPipelineImpl(
            chunker, retriever, reranker, embedder, vector_store, db
        )

        doc_id = uuid4()
        document = Document(
            source_type="markdown",
            source_uri=f"test://m2-integration/{doc_id}",
            title="M2测试文档",
            content=(
                "ERP系统操作指南\n\n"
                "1. 登录系统\n"
                "2. 创建采购订单\n"
                "3. 审核订单\n\n"
                "CRM客户管理流程\n\n"
                "1. 新建客户\n"
                "2. 跟进商机\n"
                "3. 签约成交"
            ),
        )

        try:
            chunk_ids = await pipeline.ingest(document, TID)
            assert len(chunk_ids) > 0

            chunks = await pipeline.retrieve("ERP系统操作", TID, top_k=3)
            assert len(chunks) > 0
            assert any("ERP" in c.content or "操作" in c.content for c in chunks)
        finally:
            await pipeline.delete_document(chunk_ids[0], TID) if chunk_ids else None

    async def test_rag_retrieve_seeded(self, db: DbClient) -> None:
        """Retrieve from seeded documents (ERP操作手册 / CRM API文档)."""
        from eaos.infra.vector.pgvector_store import PgVectorStore
        from eaos.knowledge.rag.retriever import HybridRetriever

        embedder = _MockEmbedder()
        vector_store = PgVectorStore(db)
        retriever = HybridRetriever(vector_store, embedder, db)

        chunks = await retriever.retrieve("ERP", TID, top_k=5)
        assert len(chunks) > 0


class TestMemory:
    async def test_store_and_recall(self, db: DbClient) -> None:
        from eaos.infra.vector.pgvector_store import PgVectorStore
        from eaos.knowledge.memory.store import (
            Memory,
            MemoryScope,
            MemoryType,
            PgMemoryStore,
        )

        embedder = _MockEmbedder()
        vector_store = PgVectorStore(db)
        store = PgMemoryStore(vector_store, embedder, db)

        mem_id = uuid4()
        user_id = uuid4()
        memory = Memory(
            id=mem_id,
            tenant_id=TID,
            scope=MemoryScope.PERSONAL,
            owner_id=user_id,
            memory_type=MemoryType.PREFERENCE,
            content="用户偏好深色主题和简洁回答",
        )

        try:
            await store.store(memory)
            recalled = await store.recall(
                "深色主题", TID, MemoryScope.PERSONAL, user_id, top_k=5
            )
            assert any(m.id == mem_id for m in recalled)
        finally:
            await store.delete(mem_id, TID)

    async def test_consolidate_session(self, db: DbClient) -> None:
        from eaos.infra.vector.pgvector_store import PgVectorStore
        from eaos.knowledge.memory.consolidator import SessionMemoryConsolidator
        from eaos.knowledge.memory.store import PgMemoryStore

        embedder = _MockEmbedder()
        vector_store = PgVectorStore(db)
        store = PgMemoryStore(vector_store, embedder, db)
        llm_response = (
            '[{"type": "preference", "content": "用户偏好中文回答"}, '
            '{"type": "fact", "content": "用户是销售部门主管"}]'
        )
        llm = _mock_llm(llm_response)
        consolidator = SessionMemoryConsolidator(store, llm, db)

        created_ids = await consolidator.consolidate_session(
            SESSION_DEMO, TID, UUID("00000000-0000-0000-0000-000000000201")
        )

        try:
            assert len(created_ids) >= 1
            memories = [await store.get(mid, TID) for mid in created_ids]
            assert any("中文" in m.content for m in memories)
        finally:
            for mid in created_ids:
                await store.delete(mid, TID)
