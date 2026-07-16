"""Unit tests for HybridRetriever — mock VectorStore + Embedder + DbClient."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from eaos.infra.vector.base import VectorSearchResult
from eaos.knowledge.rag.retriever import HybridRetriever

TID = UUID("00000000-0000-0000-0000-000000000001")


def _make_components() -> tuple[Any, Any, Any]:
    vs: Any = MagicMock()
    vs.search = AsyncMock()
    embedder: Any = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 8)
    db: Any = MagicMock()
    db.tenant_scoped_fetch = AsyncMock()
    # C05: _fetch_chunks uses db.fetch (not tenant_scoped_fetch) for chunk loading
    db.fetch = AsyncMock()
    return vs, embedder, db


def _vs_result(cid: UUID, score: float, content: str = "c") -> VectorSearchResult:
    return VectorSearchResult(id=cid, content=content, score=score, metadata={})


def _bm25_row(cid: UUID) -> dict[str, Any]:
    return {"id": cid}


def _full_row(cid: UUID, content: str = "content") -> dict[str, Any]:
    return {
        "id": cid,
        "document_id": uuid4(),
        "tenant_id": TID,
        "chunk_index": 0,
        "content": content,
        "token_count": 10,
        "metadata": {"page": 1},
        "scope": "enterprise",
        "owner_id": None,
    }


class TestRetrieve:
    async def test_calls_embed_and_vector_search(self) -> None:
        vs, embedder, db = _make_components()
        c1 = uuid4()
        vs.search.return_value = [_vs_result(c1, 0.1)]
        db.tenant_scoped_fetch.return_value = [_bm25_row(c1)]
        db.fetch.return_value = [_full_row(c1)]
        r = HybridRetriever(vs, embedder, db)
        await r.retrieve("query", TID, top_k=5)
        embedder.embed.assert_awaited_once_with("query")
        vs.search.assert_awaited_once()

    async def test_returns_empty_when_no_results(self) -> None:
        vs, embedder, db = _make_components()
        vs.search.return_value = []
        db.tenant_scoped_fetch.return_value = []
        r = HybridRetriever(vs, embedder, db)
        result = await r.retrieve("q", TID)
        assert result == []

    async def test_rrf_fuses_vector_and_bm25(self) -> None:
        vs, embedder, db = _make_components()
        c1, c2, c3 = uuid4(), uuid4(), uuid4()
        vs.search.return_value = [
            _vs_result(c1, 0.1),
            _vs_result(c2, 0.2),
            _vs_result(c3, 0.3),
        ]
        db.tenant_scoped_fetch.return_value = [_bm25_row(c3), _bm25_row(c2)]
        db.fetch.return_value = [_full_row(c1), _full_row(c2), _full_row(c3)]
        r = HybridRetriever(vs, embedder, db)
        result = await r.retrieve("q", TID, top_k=3)
        ids = [c.id for c in result]
        assert c2 in ids
        assert c3 in ids

    async def test_chunk_in_both_lists_ranks_higher(self) -> None:
        vs, embedder, db = _make_components()
        c_both = uuid4()
        c_vec_only = uuid4()
        c_bm25_only = uuid4()
        vs.search.return_value = [
            _vs_result(c_vec_only, 0.1),
            _vs_result(c_both, 0.2),
        ]
        db.tenant_scoped_fetch.return_value = [_bm25_row(c_bm25_only), _bm25_row(c_both)]
        db.fetch.return_value = [_full_row(c_both), _full_row(c_vec_only), _full_row(c_bm25_only)]
        r = HybridRetriever(vs, embedder, db)
        result = await r.retrieve("q", TID, top_k=3)
        assert result[0].id == c_both

    async def test_top_k_limit(self) -> None:
        vs, embedder, db = _make_components()
        ids = [uuid4() for _ in range(5)]
        vs.search.return_value = [_vs_result(cid, 0.1 * i) for i, cid in enumerate(ids)]
        db.tenant_scoped_fetch.return_value = [_bm25_row(cid) for cid in ids]
        db.fetch.return_value = [_full_row(cid) for cid in ids]
        r = HybridRetriever(vs, embedder, db)
        result = await r.retrieve("q", TID, top_k=3)
        assert len(result) == 3

    async def test_fetch_k_is_double_top_k(self) -> None:
        vs, embedder, db = _make_components()
        vs.search.return_value = []
        db.tenant_scoped_fetch.return_value = []
        db.fetch.return_value = []
        r = HybridRetriever(vs, embedder, db)
        await r.retrieve("q", TID, top_k=5)
        call = vs.search.call_args
        # C05: fetch_k = top_k * 3 (over-fetch to compensate for permission filtering)
        assert call.kwargs["top_k"] == 15

    async def test_returns_chunk_objects_with_full_data(self) -> None:
        vs, embedder, db = _make_components()
        c1 = uuid4()
        vs.search.return_value = [_vs_result(c1, 0.1)]
        db.tenant_scoped_fetch.return_value = [_bm25_row(c1)]
        db.fetch.return_value = [_full_row(c1, content="full content")]
        r = HybridRetriever(vs, embedder, db)
        result = await r.retrieve("q", TID, top_k=1)
        assert len(result) == 1
        assert result[0].content == "full content"
        assert result[0].token_count == 10
        assert result[0].tenant_id == TID

    async def test_bm25_search_uses_ilike(self) -> None:
        vs, embedder, db = _make_components()
        vs.search.return_value = []
        db.tenant_scoped_fetch.return_value = []
        r = HybridRetriever(vs, embedder, db)
        await r.retrieve("search term", TID)
        bm25_call = db.tenant_scoped_fetch.call_args_list[0]
        sql = bm25_call.args[0]
        assert "ILIKE" in sql
        assert "knowledge.chunks" in sql
        pattern_arg = bm25_call.args[2]
        assert pattern_arg == "%search term%"

    async def test_vector_only_chunk_still_returned(self) -> None:
        vs, embedder, db = _make_components()
        c_vec = uuid4()
        vs.search.return_value = [_vs_result(c_vec, 0.1)]
        db.tenant_scoped_fetch.return_value = []
        db.fetch.return_value = [_full_row(c_vec)]
        r = HybridRetriever(vs, embedder, db)
        result = await r.retrieve("q", TID, top_k=5)
        assert len(result) == 1
        assert result[0].id == c_vec

    async def test_bm25_only_chunk_still_returned(self) -> None:
        vs, embedder, db = _make_components()
        c_bm = uuid4()
        vs.search.return_value = []
        db.tenant_scoped_fetch.return_value = [_bm25_row(c_bm)]
        db.fetch.return_value = [_full_row(c_bm)]
        r = HybridRetriever(vs, embedder, db)
        result = await r.retrieve("q", TID, top_k=5)
        assert len(result) == 1
        assert result[0].id == c_bm
