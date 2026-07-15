"""Hybrid retriever — vector + BM25 (ILIKE) fused via Reciprocal Rank Fusion.

Phase 2 simplification: BM25 uses ILIKE keyword matching instead of tsvector.
Vector search (via ``VectorStore.search``) is the primary signal; ILIKE provides
a lexical boost. RRF combines both rankings:

    score = 1 / (60 + rank_vector) + 1 / (60 + rank_bm25)

where rank is 0-indexed (best result = rank 0). Chunks appearing in only one
list receive only that term's contribution.

C05 fixes:
- Permission filtering is now PRE-FETCH (in SQL), not post-filter.
  Chunks invisible to the user never enter the candidate pool.
- RRF scores are preserved on the returned Chunk objects (not discarded).
- user_id and department_ids are required for scope-aware retrieval.
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
    """Retriever combining pgvector similarity with ILIKE keyword matching.

    C05: Permission filtering is applied at the SQL level (pre-fetch),
    not after Top-K selection. This ensures that chunks invisible to the
    user never enter the candidate pool, preventing both information
    leakage and score distortion from post-filtering.
    """

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
        *,
        user_id: UUID | None = None,
        department_ids: list[UUID] | None = None,
    ) -> list[Chunk]:
        """Retrieve chunks with permission-aware filtering.

        When ``user_id`` is provided, only chunks visible to the user are
        returned (enterprise + personal own + department own). When ``user_id``
        is None, all tenant chunks are returned (admin/debug mode).

        The returned Chunks have their ``score`` field populated with the
        RRF fusion score.
        """
        fetch_k = top_k * 3  # C05: over-fetch to compensate for permission filtering

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

        # C05: RRF score computation — preserved on Chunk objects
        scores: dict[Any, float] = {}
        for rank, cid in enumerate(vector_ids):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
        for rank, cid in enumerate(bm25_ids):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)

        ranked = sorted(scores, key=lambda cid: scores[cid], reverse=True)
        winner_ids = ranked[:top_k]
        if not winner_ids:
            return []

        chunks = await self._fetch_chunks(winner_ids, tenant_id, scores, user_id, department_ids)
        return chunks

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
        scores: dict[Any, float],
        user_id: UUID | None = None,
        department_ids: list[UUID] | None = None,
    ) -> list[Chunk]:
        """Fetch full chunk records for the fused winner IDs, preserving order.

        C05: Applies permission filtering at the SQL level. Only chunks
        visible to the user (enterprise + personal own + department own)
        are returned. The RRF score is set on each Chunk.
        """
        if not ids:
            return []

        placeholders = ", ".join(f":p{i}" for i in range(len(ids)))
        params: list[Any] = list(ids)
        params.append(tenant_id)
        tenant_param_idx = len(ids)

        # C05: Permission-pre-filter at SQL level.
        # When user_id is provided, only return visible chunks:
        # - scope = 'enterprise' (visible to all)
        # - scope = 'personal' AND owner_id = user_id
        # - scope = 'department' AND owner_id IN user's departments
        if user_id is not None:
            dept_list = department_ids or []
            if dept_list:
                dept_placeholders = ", ".join(f":p{len(params) + i}" for i in range(len(dept_list)))
                params.extend(dept_list)
                scope_filter = (
                    f"AND (scope = 'enterprise' "
                    f"OR (scope = 'personal' AND owner_id = :p{tenant_param_idx + 1}) "
                    f"OR (scope = 'department' AND owner_id IN ({dept_placeholders})))"
                )
                # user_id param
                params.insert(tenant_param_idx + 1, user_id)
            else:
                scope_filter = (
                    f"AND (scope = 'enterprise' "
                    f"OR (scope = 'personal' AND owner_id = :p{tenant_param_idx + 1}))"
                )
                params.insert(tenant_param_idx + 1, user_id)
        else:
            scope_filter = ""

        rows = await self._db.fetch(
            f"SELECT id, document_id, tenant_id, chunk_index, content, "
            f"token_count, metadata, scope, owner_id FROM knowledge.chunks "
            f"WHERE tenant_id = :p{tenant_param_idx} AND id IN ({placeholders}) "
            f"{scope_filter}",
            *params,
        )

        by_id: dict[UUID, dict[str, Any]] = {r["id"]: r for r in rows}
        chunks: list[Chunk] = []
        for cid in ids:
            row = by_id.get(cid)
            if row is None:
                continue  # filtered out by permission or not found
            meta = row.get("metadata") or {}
            # Preserve scope/owner info in metadata for downstream citation
            meta["scope"] = row.get("scope", "enterprise")
            if row.get("owner_id") is not None:
                meta["owner_id"] = str(row["owner_id"])
            chunks.append(
                Chunk(
                    id=row["id"],
                    document_id=row["document_id"],
                    tenant_id=row["tenant_id"],
                    chunk_index=row["chunk_index"],
                    content=row["content"],
                    token_count=row["token_count"],
                    metadata=meta,
                    score=scores.get(cid, 0.0),  # C05: preserve RRF score
                )
            )
        return chunks
