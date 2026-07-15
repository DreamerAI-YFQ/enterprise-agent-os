"""Hybrid retriever — vector + BM25 (ILIKE) fused via Reciprocal Rank Fusion.

Phase 2 simplification: BM25 uses ILIKE keyword matching instead of tsvector.
Vector search (via ``VectorStore.search``) is the primary signal; ILIKE provides
a lexical boost. RRF combines both rankings:

    score = 1 / (60 + rank_vector) + 1 / (60 + rank_bm25)

where rank is 0-indexed (best result = rank 0). Chunks appearing in only one
list receive only that term's contribution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eaos.knowledge.rag.pipeline import Chunk

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient
    from eaos.infra.vector.base import Embedder, VectorStore


_RRF_K = 60  # RRF constant (standard value from the original paper).


class HybridRetriever:
    """Retriever combining pgvector similarity with ILIKE keyword matching."""

    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        db: DbClient,
    ) -> None:
        self._vs = vector_store
        self._embedder = embedder
        self._db = db

    async def retrieve(
        self,
        query: str,
        tenant_id: UUID,
        top_k: int = 10,
    ) -> list[Chunk]:
        fetch_k = top_k * 2

        embedding = await self._embedder.embed(query)
        vector_results = await self._vs.search(
            embedding,
            tenant_id,
            "knowledge.chunks",
            top_k=fetch_k,
        )
        bm25_rows = await self._bm25_search(query, tenant_id, fetch_k)

        vector_ids = [r.id for r in vector_results]
        bm25_ids = [r["id"] for r in bm25_rows]

        scores: dict[UUID, float] = {}
        for rank, cid in enumerate(vector_ids):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
        for rank, cid in enumerate(bm25_ids):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)

        ranked = sorted(scores, key=lambda cid: scores[cid], reverse=True)
        winner_ids = ranked[:top_k]
        if not winner_ids:
            return []

        return await self._fetch_chunks(winner_ids, tenant_id)

    async def _bm25_search(
        self,
        query: str,
        tenant_id: UUID,
        limit: int,
    ) -> list[dict[str, Any]]:
        """ILIKE keyword match on chunk content (Phase 2 BM25 substitute)."""
        pattern = f"%{query}%"
        rows = await self._db.tenant_scoped_fetch(
            "SELECT id FROM knowledge.chunks "
            "WHERE tenant_id = :tenant_id AND content ILIKE :p0 "
            "ORDER BY length(content) LIMIT :p1",
            tenant_id,
            pattern,
            limit,
        )
        return rows

    async def _fetch_chunks(
        self,
        ids: list[UUID],
        tenant_id: UUID,
    ) -> list[Chunk]:
        """Fetch full chunk records for the fused winner IDs, preserving order."""
        if not ids:
            return []
        placeholders = ", ".join(f":p{i}" for i in range(len(ids)))
        rows = await self._db.tenant_scoped_fetch(
            f"SELECT id, document_id, tenant_id, chunk_index, content, "
            f"token_count, metadata FROM knowledge.chunks "
            f"WHERE tenant_id = :tenant_id AND id IN ({placeholders})",
            tenant_id,
            *ids,
        )
        by_id: dict[UUID, dict[str, Any]] = {r["id"]: r for r in rows}
        chunks: list[Chunk] = []
        for cid in ids:
            row = by_id.get(cid)
            if row is None:
                continue
            meta = row.get("metadata") or {}
            chunks.append(
                Chunk(
                    id=row["id"],
                    document_id=row["document_id"],
                    tenant_id=row["tenant_id"],
                    chunk_index=row["chunk_index"],
                    content=row["content"],
                    token_count=row["token_count"],
                    metadata=meta,
                )
            )
        return chunks
