"""Unit tests for RAGPipelineImpl — mock all dependencies."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from eaos.knowledge.rag.pipeline import Chunk, Document, RAGPipelineImpl

TID = UUID("00000000-0000-0000-0000-000000000001")


def _make_pipeline(
    chunks: list[Chunk] | None = None,
    retrieved: list[Chunk] | None = None,
    reranked: list[Chunk] | None = None,
) -> tuple[RAGPipelineImpl, dict[str, Any]]:
    chunker: Any = MagicMock()
    chunker.chunk = AsyncMock(return_value=chunks or [])
    retriever: Any = MagicMock()
    retriever.retrieve = AsyncMock(return_value=retrieved or [])
    reranker: Any = MagicMock()
    reranker.rerank = AsyncMock(return_value=reranked or [])
    embedder: Any = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 8)
    vs: Any = MagicMock()
    vs.insert = AsyncMock()
    db: Any = MagicMock()
    db.execute = AsyncMock()

    pipeline = RAGPipelineImpl(chunker, retriever, reranker, embedder, vs, db)
    return pipeline, {
        "chunker": chunker,
        "retriever": retriever,
        "reranker": reranker,
        "embedder": embedder,
        "vs": vs,
        "db": db,
    }


def _make_chunk(content: str = "content") -> Chunk:
    return Chunk(
        id=uuid4(),
        document_id=uuid4(),
        tenant_id=TID,
        chunk_index=0,
        content=content,
        token_count=10,
        metadata={"page": 1},
    )


def _make_doc(content: str = "doc content") -> Document:
    return Document(
        source_type="text",
        source_uri="mem://test",
        title="Test",
        content=content,
    )


class TestIngest:
    async def test_empty_chunks_returns_empty(self) -> None:
        pipeline, deps = _make_pipeline(chunks=[])
        result = await pipeline.ingest(_make_doc(), TID)
        assert result == []
        deps["db"].execute.assert_not_awaited()
        deps["vs"].insert.assert_not_awaited()

    async def test_inserts_document_and_chunks(self) -> None:
        c1, c2 = _make_chunk("a"), _make_chunk("b")
        pipeline, deps = _make_pipeline(chunks=[c1, c2])
        result = await pipeline.ingest(_make_doc("hello"), TID)
        assert len(result) == 2
        deps["db"].execute.assert_awaited_once()
        deps["vs"].insert.assert_awaited_once()

    async def test_document_uses_chunk_document_id(self) -> None:
        c1 = _make_chunk("a")
        pipeline, deps = _make_pipeline(chunks=[c1])
        await pipeline.ingest(_make_doc(), TID)
        call = deps["db"].execute.call_args
        assert call.args[1] == c1.document_id

    async def test_content_hash_computed(self) -> None:
        c1 = _make_chunk("a")
        pipeline, deps = _make_pipeline(chunks=[c1])
        import hashlib

        expected = hashlib.sha256(b"hello").hexdigest()
        await pipeline.ingest(_make_doc("hello"), TID)
        call = deps["db"].execute.call_args
        content_hash_arg = call.args[6]
        assert content_hash_arg == expected

    async def test_embeds_each_chunk(self) -> None:
        c1, c2, c3 = _make_chunk("a"), _make_chunk("b"), _make_chunk("c")
        pipeline, deps = _make_pipeline(chunks=[c1, c2, c3])
        await pipeline.ingest(_make_doc(), TID)
        assert deps["embedder"].embed.await_count == 3

    async def test_returns_chunk_ids(self) -> None:
        c1, c2 = _make_chunk("a"), _make_chunk("b")
        pipeline, _ = _make_pipeline(chunks=[c1, c2])
        result = await pipeline.ingest(_make_doc(), TID)
        assert result == [c1.id, c2.id]


class TestRetrieve:
    async def test_calls_retriever_then_reranker(self) -> None:
        c1 = _make_chunk("a")
        pipeline, deps = _make_pipeline(retrieved=[c1], reranked=[c1])
        await pipeline.retrieve("query", TID, top_k=5)
        deps["retriever"].retrieve.assert_awaited_once()
        deps["reranker"].rerank.assert_awaited_once()

    async def test_retriever_uses_double_top_k(self) -> None:
        c1 = _make_chunk("a")
        pipeline, deps = _make_pipeline(retrieved=[c1], reranked=[c1])
        await pipeline.retrieve("q", TID, top_k=5)
        call = deps["retriever"].retrieve.call_args
        assert call.kwargs["top_k"] == 10

    async def test_returns_reranked_result(self) -> None:
        c1, c2 = _make_chunk("a"), _make_chunk("b")
        pipeline, _ = _make_pipeline(retrieved=[c1, c2], reranked=[c2, c1])
        result = await pipeline.retrieve("q", TID, top_k=5)
        assert result == [c2, c1]

    async def test_empty_retrieval_returns_empty(self) -> None:
        pipeline, _ = _make_pipeline(retrieved=[], reranked=[])
        result = await pipeline.retrieve("q", TID, top_k=5)
        assert result == []


class TestDeleteDocument:
    async def test_deletes_from_documents_table(self) -> None:
        pipeline, deps = _make_pipeline()
        doc_id = uuid4()
        await pipeline.delete_document(doc_id, TID)
        deps["db"].execute.assert_awaited_once()
        sql = deps["db"].execute.call_args.args[0]
        assert "DELETE FROM knowledge.documents" in sql
        assert deps["db"].execute.call_args.args[1] == doc_id
        assert deps["db"].execute.call_args.args[2] == TID
