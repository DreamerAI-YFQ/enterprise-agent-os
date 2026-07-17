"""Unit tests for HybridRetriever — mock VectorStore + Embedder + DbClient."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from eaos.infra.vector.base import VectorSearchResult
from eaos.knowledge.rag.retriever import HybridRetriever, extract_structured_identifiers

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


def _full_row(
    cid: UUID,
    content: str = "content",
    *,
    document_id: UUID | None = None,
    chunk_index: int = 0,
) -> dict[str, Any]:
    return {
        "id": cid,
        "document_id": document_id or uuid4(),
        "tenant_id": TID,
        "chunk_index": chunk_index,
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

    async def test_fetch_k_retains_five_times_top_k_candidates(self) -> None:
        vs, embedder, db = _make_components()
        vs.search.return_value = []
        db.tenant_scoped_fetch.return_value = []
        db.fetch.return_value = []
        r = HybridRetriever(vs, embedder, db)
        await r.retrieve("q", TID, top_k=5)
        call = vs.search.call_args
        assert call.kwargs["top_k"] == 25

    async def test_document_diversity_softly_limits_dominant_document(self) -> None:
        vs, embedder, db = _make_components()
        dominant_document = uuid4()
        other_document = uuid4()
        third_document = uuid4()
        a1, a2, a3, a4, b1, c1 = [uuid4() for _ in range(6)]
        ranked_ids = [a1, a2, a3, a4, b1, c1]
        vs.search.return_value = [
            _vs_result(chunk_id, float(rank))
            for rank, chunk_id in enumerate(ranked_ids)
        ]
        db.tenant_scoped_fetch.return_value = []
        db.fetch.return_value = [
            _full_row(a1, document_id=dominant_document, chunk_index=0),
            _full_row(a2, document_id=dominant_document, chunk_index=1),
            _full_row(a3, document_id=dominant_document, chunk_index=2),
            _full_row(a4, document_id=dominant_document, chunk_index=3),
            _full_row(b1, document_id=other_document),
            _full_row(c1, document_id=third_document),
        ]
        r = HybridRetriever(vs, embedder, db)

        result = await r.retrieve("跨文档汇总", TID, top_k=5)

        assert [chunk.id for chunk in result] == [a1, a2, b1, c1, a3]

    async def test_document_diversity_backfills_single_document_in_rank_order(self) -> None:
        vs, embedder, db = _make_components()
        document_id = uuid4()
        ids = [uuid4() for _ in range(4)]
        vs.search.return_value = [
            _vs_result(chunk_id, float(rank))
            for rank, chunk_id in enumerate(ids)
        ]
        db.tenant_scoped_fetch.return_value = []
        db.fetch.return_value = [
            _full_row(
                chunk_id,
                document_id=document_id,
                chunk_index=rank,
            )
            for rank, chunk_id in enumerate(ids)
        ]
        r = HybridRetriever(vs, embedder, db)

        result = await r.retrieve("单文档事实", TID, top_k=4)

        assert [chunk.id for chunk in result] == ids

    async def test_structured_identifier_uses_same_soft_document_diversity(self) -> None:
        vs, embedder, db = _make_components()
        identity_document = uuid4()
        related_document = uuid4()
        third_document = uuid4()
        a1, a2, a3, a4, b1, c1 = [uuid4() for _ in range(6)]
        ranked_ids = [a1, a2, a3, a4, b1, c1]
        db.tenant_scoped_fetch.return_value = [
            _bm25_row(chunk_id) for chunk_id in ranked_ids
        ]
        vs.search.return_value = []
        db.fetch.return_value = [
            _full_row(a1, document_id=identity_document, chunk_index=0),
            _full_row(a2, document_id=identity_document, chunk_index=1),
            _full_row(a3, document_id=identity_document, chunk_index=2),
            _full_row(a4, document_id=identity_document, chunk_index=3),
            _full_row(b1, document_id=related_document),
            _full_row(c1, document_id=third_document),
        ]
        r = HybridRetriever(vs, embedder, db)

        result = await r.retrieve("PRD-001 的关联记录", TID, top_k=5)

        assert [chunk.id for chunk in result] == [a1, a2, b1, c1, a3]
        embedder.embed.assert_awaited_once_with("PRD-001 的关联记录")
        vs.search.assert_awaited_once()

    async def test_structured_identifier_vector_boosts_query_relevant_chunk(self) -> None:
        vs, embedder, db = _make_components()
        stale_chunk = uuid4()
        relevant_chunk = uuid4()
        document_id = uuid4()
        db.tenant_scoped_fetch.return_value = [
            _bm25_row(stale_chunk),
            _bm25_row(relevant_chunk),
        ]
        vs.search.return_value = [_vs_result(relevant_chunk, 0.01)]
        db.fetch.return_value = [
            _full_row(stale_chunk, "## 联系方式", document_id=document_id),
            _full_row(
                relevant_chunk,
                "## 基本信息\n- 信用额度: 80万元",
                document_id=document_id,
                chunk_index=1,
            ),
        ]
        r = HybridRetriever(vs, embedder, db)

        result = await r.retrieve("客户 CUS-001 的信用额度是多少？", TID, top_k=2)

        assert [chunk.id for chunk in result] == [relevant_chunk, stale_chunk]

    async def test_single_entity_detail_keeps_identity_document_window(self) -> None:
        vs, embedder, db = _make_components()
        identity_document = uuid4()
        related_document = uuid4()
        identity_ids = [uuid4() for _ in range(4)]
        related_id = uuid4()
        db.tenant_scoped_fetch.return_value = [
            *[_bm25_row(chunk_id) for chunk_id in identity_ids],
            _bm25_row(related_id),
        ]
        # The related row is semantically strongest, but a one-record detail
        # answer still needs the complete four-chunk identity document.
        vs.search.return_value = [_vs_result(related_id, 0.01)]
        db.fetch.return_value = [
            *[
                _full_row(
                    chunk_id,
                    document_id=identity_document,
                    chunk_index=index,
                )
                for index, chunk_id in enumerate(identity_ids)
            ],
            _full_row(related_id, document_id=related_document),
        ]
        r = HybridRetriever(vs, embedder, db)

        result = await r.retrieve("PRD-002 的规格参数是什么？", TID, top_k=5)

        assert {chunk.id for chunk in result[:4]} == set(identity_ids)
        assert result[4].id == related_id

    async def test_sku_and_model_identifiers_still_form_one_detail_query(self) -> None:
        vs, embedder, db = _make_components()
        identity_document = uuid4()
        identity_ids = [uuid4() for _ in range(4)]
        related_id = uuid4()
        db.tenant_scoped_fetch.return_value = [
            *[_bm25_row(chunk_id) for chunk_id in identity_ids],
            _bm25_row(related_id),
        ]
        vs.search.return_value = [_vs_result(related_id, 0.01)]
        db.fetch.return_value = [
            *[
                _full_row(chunk_id, document_id=identity_document, chunk_index=index)
                for index, chunk_id in enumerate(identity_ids)
            ],
            _full_row(related_id),
        ]
        r = HybridRetriever(vs, embedder, db)

        result = await r.retrieve(
            "PRD-002 企业交换机 SW-4800 的规格参数是什么？",
            TID,
            top_k=5,
        )

        assert {chunk.id for chunk in result[:4]} == set(identity_ids)

    async def test_structured_identifier_vector_failure_preserves_lexical_results(
        self,
    ) -> None:
        vs, embedder, db = _make_components()
        chunk_id = uuid4()
        db.tenant_scoped_fetch.return_value = [_bm25_row(chunk_id)]
        embedder.embed.side_effect = RuntimeError("embedding unavailable")
        db.fetch.return_value = [_full_row(chunk_id)]
        r = HybridRetriever(vs, embedder, db)

        result = await r.retrieve("KB-POL-003", TID)

        assert [chunk.id for chunk in result] == [chunk_id]
        vs.search.assert_not_awaited()

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

    async def test_structured_identifier_uses_lexical_visibility_without_vector(self) -> None:
        vs, embedder, db = _make_components()
        vs.search.return_value = []
        db.tenant_scoped_fetch.return_value = []
        user_id = uuid4()
        department_ids = [uuid4(), uuid4()]
        r = HybridRetriever(vs, embedder, db)

        await r.retrieve(
            "PRD-001 的价格",
            TID,
            top_k=5,
            user_id=user_id,
            department_ids=department_ids,
        )

        embedder.embed.assert_not_awaited()
        vs.search.assert_not_awaited()

        keyword_call = db.tenant_scoped_fetch.call_args
        sql = keyword_call.args[0]
        assert "c.scope = 'enterprise'" in sql
        assert "c.scope = 'personal' AND c.owner_id = :p2" in sql
        assert "c.scope = 'department' AND c.owner_id IN (:p3, :p4)" in sql
        assert keyword_call.args[2:] == (
            "%PRD-001 的价格%",
            "%PRD-001%",
            user_id,
            *department_ids,
            25,
        )
        assert "LIMIT :p5" in sql

    async def test_structured_identifier_with_no_lexical_hit_returns_empty(self) -> None:
        vs, embedder, db = _make_components()
        vector_only = uuid4()
        vs.search.return_value = [_vs_result(vector_only, 0.99)]
        db.tenant_scoped_fetch.return_value = []
        r = HybridRetriever(vs, embedder, db)

        result = await r.retrieve("KB-GLOBEX-SECRET-001", TID)

        assert result == []
        embedder.embed.assert_not_awaited()
        vs.search.assert_not_awaited()
        db.fetch.assert_not_awaited()

    async def test_structured_identifier_returns_exact_lexical_hit(self) -> None:
        vs, embedder, db = _make_components()
        chunk_id = uuid4()
        db.tenant_scoped_fetch.return_value = [_bm25_row(chunk_id)]
        vs.search.return_value = []
        db.fetch.return_value = [_full_row(chunk_id)]
        r = HybridRetriever(vs, embedder, db)

        result = await r.retrieve("KB-POL-003", TID)

        assert [chunk.id for chunk in result] == [chunk_id]
        embedder.embed.assert_awaited_once_with("KB-POL-003")
        vs.search.assert_awaited_once()

    async def test_final_fetch_visibility_placeholders_are_not_shifted(self) -> None:
        vs, embedder, db = _make_components()
        chunk_id = uuid4()
        user_id = uuid4()
        department_ids = [uuid4(), uuid4()]
        vs.search.return_value = [_vs_result(chunk_id, 0.1)]
        db.tenant_scoped_fetch.return_value = []
        db.fetch.return_value = [_full_row(chunk_id)]
        r = HybridRetriever(vs, embedder, db)

        await r.retrieve(
            "query",
            TID,
            user_id=user_id,
            department_ids=department_ids,
        )

        fetch_call = db.fetch.call_args
        sql = fetch_call.args[0]
        assert "tenant_id = :p1" in sql
        assert "scope = 'personal' AND owner_id = :p2" in sql
        assert "scope = 'department' AND owner_id IN (:p3, :p4)" in sql
        assert fetch_call.args[1:] == (chunk_id, TID, user_id, *department_ids)

    async def test_structured_identifiers_search_document_identity_fields(self) -> None:
        vs, embedder, db = _make_components()
        vs.search.return_value = []
        db.tenant_scoped_fetch.return_value = []
        r = HybridRetriever(vs, embedder, db)

        await r.retrieve("比较 PRD-002 与 SW-4800", TID)

        keyword_call = db.tenant_scoped_fetch.call_args
        sql = keyword_call.args[0]
        assert "JOIN knowledge.documents" in sql
        assert "d.title ILIKE" in sql
        assert "d.source_uri ILIKE" in sql
        assert "d.metadata->>'doc_id' ILIKE" in sql
        assert "CASE WHEN" in sql
        assert "THEN 0" in sql
        assert "THEN 1" in sql
        assert "THEN 2" in sql
        assert keyword_call.args[2:] == (
            "%比较 PRD-002 与 SW-4800%",
            "%PRD-002%",
            "%SW-4800%",
            50,
        )


def test_extract_structured_identifiers_is_deterministic() -> None:
    assert extract_structured_identifiers(
        "比较 prd-002、SW-4800 和 prd-002，再看 ORD-2024-001"
    ) == ["prd-002", "SW-4800", "ORD-2024-001"]
    assert extract_structured_identifiers("查询 G-ORD-TEST") == ["G-ORD-TEST"]
