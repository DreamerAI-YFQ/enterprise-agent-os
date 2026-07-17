"""Unit tests for KnowledgeEngineImpl — mock all sub-modules."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from eaos.knowledge.engine import KnowledgeEngineImpl, SearchResult
from eaos.knowledge.memory.store import Memory, MemoryScope, MemoryType
from eaos.knowledge.ontology.query_rewrite import RewrittenQuery
from eaos.knowledge.rag.pipeline import Chunk, Document

TID = UUID("00000000-0000-0000-0000-000000000001")


def _make_engine() -> tuple[KnowledgeEngineImpl, dict[str, Any]]:
    ontology_repo: Any = MagicMock()
    rewriter: Any = MagicMock()
    rewriter.rewrite = AsyncMock()
    rag: Any = MagicMock()
    rag.ingest = AsyncMock()
    rag.retrieve = AsyncMock()
    memory_store: Any = MagicMock()
    memory_store.recall = AsyncMock()
    consolidator: Any = MagicMock()

    engine = KnowledgeEngineImpl(ontology_repo, rewriter, rag, memory_store, consolidator)
    return engine, {
        "rewriter": rewriter,
        "rag": rag,
        "memory_store": memory_store,
    }


def _rewritten_query(text: str = "rewritten") -> RewrittenQuery:
    return RewrittenQuery(
        original="orig",
        rewritten=text,
        entities=[],
        expansion_notes="",
        ontology_refs=[],
    )


def _make_chunk(content: str = "chunk content") -> Chunk:
    return Chunk(
        id=uuid4(),
        document_id=uuid4(),
        tenant_id=TID,
        chunk_index=0,
        content=content,
        token_count=10,
        metadata={"page": 1},
    )


def _make_memory() -> Memory:
    return Memory(
        id=uuid4(),
        tenant_id=TID,
        scope=MemoryScope.PERSONAL,
        owner_id=uuid4(),
        memory_type=MemoryType.PREFERENCE,
        content="prefers dark mode",
    )


class TestSearch:
    async def test_rewrites_then_retrieves(self) -> None:
        engine, deps = _make_engine()
        deps["rewriter"].rewrite.return_value = _rewritten_query("expanded query")
        deps["rag"].retrieve.return_value = [_make_chunk("result")]
        await engine.search("query", TID, top_k=5)
        deps["rewriter"].rewrite.assert_awaited_once_with("query", TID)
        # C05: search now passes user_id and department_ids to retrieve
        deps["rag"].retrieve.assert_awaited_once_with(
            "expanded query", TID, top_k=5, user_id=None, department_ids=None
        )

    async def test_returns_search_results(self) -> None:
        engine, deps = _make_engine()
        deps["rewriter"].rewrite.return_value = _rewritten_query()
        deps["rag"].retrieve.return_value = [_make_chunk("a"), _make_chunk("b")]
        result = await engine.search("q", TID)
        assert len(result) == 2
        assert all(isinstance(r, SearchResult) for r in result)
        assert result[0].content == "a"
        assert result[0].source == "rag"
        # C05: score comes from chunk's RRF score (default 0.0, not hardcoded 1.0)
        assert result[0].score == 0.0

    async def test_empty_results(self) -> None:
        engine, deps = _make_engine()
        deps["rewriter"].rewrite.return_value = _rewritten_query()
        deps["rag"].retrieve.return_value = []
        result = await engine.search("q", TID)
        assert result == []

    async def test_metadata_copied(self) -> None:
        engine, deps = _make_engine()
        deps["rewriter"].rewrite.return_value = _rewritten_query()
        chunk = _make_chunk("x")
        doc_id = uuid4()
        chunk = Chunk(
            id=chunk.id,
            document_id=doc_id,
            tenant_id=chunk.tenant_id,
            chunk_index=0,
            content="x",
            token_count=1,
            metadata={"key": "val"},
        )
        deps["rag"].retrieve.return_value = [chunk]
        result = await engine.search("q", TID)
        # C13/Fix-A: document_id is injected into metadata for citation/eval
        assert result[0].metadata["key"] == "val"
        assert result[0].metadata["document_id"] == str(doc_id)

    async def test_rewrite_exception_falls_back_to_original_query(self) -> None:
        engine, deps = _make_engine()
        deps["rewriter"].rewrite.side_effect = TimeoutError("provider timeout")
        deps["rag"].retrieve.return_value = []

        await engine.search("original query", TID)

        deps["rag"].retrieve.assert_awaited_once_with(
            "original query", TID, top_k=5, user_id=None, department_ids=None
        )

    async def test_structured_identifier_skips_llm_rewrite(self) -> None:
        engine, deps = _make_engine()
        deps["rag"].retrieve.return_value = []

        await engine.search("查询 KB-POL-003", TID)

        deps["rewriter"].rewrite.assert_not_awaited()
        deps["rag"].retrieve.assert_awaited_once_with(
            "查询 KB-POL-003",
            TID,
            top_k=5,
            user_id=None,
            department_ids=None,
        )

    async def test_loads_tenant_checked_department_memberships(self) -> None:
        engine, deps = _make_engine()
        db: Any = MagicMock()
        department_id = uuid4()
        db.fetch = AsyncMock(return_value=[{"department_id": department_id}])
        engine._db = db
        user_id = uuid4()
        deps["rewriter"].rewrite.return_value = _rewritten_query("expanded")
        deps["rag"].retrieve.return_value = []

        await engine.search("query", TID, user_id=user_id)

        membership_call = db.fetch.call_args
        assert "iam.memberships" in membership_call.args[0]
        assert "JOIN iam.departments" in membership_call.args[0]
        assert membership_call.args[1:] == (user_id, TID)
        deps["rag"].retrieve.assert_awaited_once_with(
            "expanded",
            TID,
            top_k=5,
            user_id=user_id,
            department_ids=[department_id],
        )


class TestIngestDocument:
    async def test_delegates_to_rag(self) -> None:
        engine, deps = _make_engine()
        chunk_ids = [uuid4(), uuid4()]
        deps["rag"].ingest.return_value = chunk_ids
        doc = Document(source_type="text", source_uri="mem://t", title="T", content="c")
        result = await engine.ingest_document(doc, TID)
        deps["rag"].ingest.assert_awaited_once_with(doc, TID)
        assert result == chunk_ids


class TestRecallMemory:
    async def test_delegates_to_memory_store(self) -> None:
        engine, deps = _make_engine()
        mem = _make_memory()
        deps["memory_store"].recall.return_value = [mem]
        owner = uuid4()
        result = await engine.recall_memory("q", TID, MemoryScope.PERSONAL, owner, top_k=3)
        deps["memory_store"].recall.assert_awaited_once_with(
            "q", TID, MemoryScope.PERSONAL, owner, 3
        )
        assert result == [mem]


class TestRewriteQuery:
    async def test_delegates_to_rewriter(self) -> None:
        engine, deps = _make_engine()
        rq = _rewritten_query("disambiguated")
        deps["rewriter"].rewrite.return_value = rq
        result = await engine.rewrite_query("ambiguous query", TID)
        deps["rewriter"].rewrite.assert_awaited_once_with("ambiguous query", TID)
        assert result.rewritten == "disambiguated"
