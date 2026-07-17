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

import logging
import re
from typing import TYPE_CHECKING, Any

from eaos.knowledge.rag.pipeline import Chunk

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient
    from eaos.infra.vector.base import Embedder, VectorStore


RRF_K = 60  # RRF constant (standard value from the original paper).
_CANDIDATE_POOL_MULTIPLIER = 5
_PREFERRED_CHUNKS_PER_DOCUMENT = 2
_ENTITY_DETAIL_CHUNK_WINDOW = 4
_RELATIONAL_QUERY_MARKERS = (
    "哪些",
    "列出",
    "所有",
    "每个",
    "订单中",
    "购买",
    "采购",
    "关联",
    "涉及",
    "最近一笔",
    "有几笔",
    "哪些仓库",
    "比较",
)
_STRUCTURED_IDENTIFIER_RE = re.compile(
    r"(?<![A-Z0-9])(?:[A-Z][A-Z0-9]*)(?:[-_][A-Z0-9]+)+(?![A-Z0-9])",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)


def extract_structured_identifiers(query: str) -> list[str]:
    """Return stable business identifiers in first-seen order.

    Examples include ``PRD-001``, ``ORD-2024-001``, ``SFP-10G`` and
    ``KB-POL-003``. These tokens are deterministic lexical signals and are
    intentionally much narrower than general-purpose tokenisation.
    """
    identifiers: list[str] = []
    seen: set[str] = set()
    for match in _STRUCTURED_IDENTIFIER_RE.finditer(query):
        identifier = match.group(0)
        normalized = identifier.casefold()
        if normalized not in seen:
            identifiers.append(identifier)
            seen.add(normalized)
    return identifiers


def _is_single_entity_detail_query(query: str, identifiers: list[str]) -> bool:
    """Distinguish entity detail lookup from relational/list exploration.

    One entity can legitimately have multiple identifiers in the same query
    (for example a SKU plus model number). Relational language, rather than the
    raw identifier count, determines whether document diversity is preferred.
    """

    return bool(identifiers) and not any(
        marker in query for marker in _RELATIONAL_QUERY_MARKERS
    )


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
        if top_k <= 0:
            return []

        # Chunk-level nearest-neighbour rankings can be dominated by the
        # sections of one long document.  Retain a bounded, deeper pool so the
        # final selector can surface other relevant documents without changing
        # either retrieval branch's relevance calculation.
        fetch_k = top_k * _CANDIDATE_POOL_MULTIPLIER

        bm25_rows = await self._bm25_search(
            query,
            tenant_id,
            fetch_k,
            user_id=user_id,
            department_ids=department_ids,
        )

        identifiers = extract_structured_identifiers(query)
        if identifiers:
            # An exact identifier is a security boundary as well as a strong
            # relevance signal. If tenant-scoped lexical lookup has no match,
            # do not let semantically similar vector-only rows cross into the
            # candidate pool. Once the lexical gate succeeds, fuse a real
            # permission-filtered vector ranking so the chunk that answers an
            # attribute query (for example ``信用额度`` or ``规格参数``) is not
            # displaced merely because another chunk is shorter.
            bm25_ids = [row["id"] for row in bm25_rows]
            if not bm25_ids:
                return []
            exact_scores: dict[Any, float] = {
                chunk_id: 1.0 / (RRF_K + rank)
                for rank, chunk_id in enumerate(bm25_ids)
            }
            try:
                embedding = await self._embedder.embed(query)
                vector_results = await self._vs.search(
                    embedding,
                    tenant_id,
                    "knowledge.chunks",
                    top_k=fetch_k,
                    visibility_user_id=user_id,
                    visibility_department_ids=department_ids,
                )
            except Exception:  # noqa: BLE001 - exact lexical evidence remains usable
                logger.warning(
                    "identifier vector ranking failed; preserving lexical order",
                    exc_info=True,
                )
                vector_results = []
            for rank, item in enumerate(vector_results):
                exact_scores[item.id] = exact_scores.get(item.id, 0.0) + 1.0 / (
                    RRF_K + rank
                )
            ranked_ids = sorted(
                exact_scores,
                key=lambda chunk_id: exact_scores[chunk_id],
                reverse=True,
            )
            entity_detail_query = _is_single_entity_detail_query(query, identifiers)
            if entity_detail_query:
                # The lexical SQL ranks identity-field matches before title and
                # content matches. Keep the first document-sized identity
                # window ahead of related records, while preserving vector RRF
                # order *within* that window. This supplies complete attributes
                # for one-record questions without weakening diversity for
                # relational/list questions such as "哪些订单购买过 PRD-001".
                identity_window = set(bm25_ids[:_ENTITY_DETAIL_CHUNK_WINDOW])
                ranked_ids = [
                    *[chunk_id for chunk_id in ranked_ids if chunk_id in identity_window],
                    *[chunk_id for chunk_id in ranked_ids if chunk_id not in identity_window],
                ]
            candidates = await self._fetch_chunks(
                ranked_ids,
                tenant_id,
                exact_scores,
                user_id,
                department_ids,
            )
            if entity_detail_query:
                return candidates[:top_k]
            return self._document_diverse_top_k(candidates, top_k)

        embedding = await self._embedder.embed(query)
        vector_results = await self._vs.search(
            embedding,
            tenant_id,
            "knowledge.chunks",
            top_k=fetch_k,
            visibility_user_id=user_id,
            visibility_department_ids=department_ids,
        )

        vector_ids = [r.id for r in vector_results]
        bm25_ids = [r["id"] for r in bm25_rows]

        # C05: RRF score computation — preserved on Chunk objects
        scores: dict[Any, float] = {}
        for rank, cid in enumerate(vector_ids):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
        for rank, cid in enumerate(bm25_ids):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)

        ranked = sorted(scores, key=lambda cid: scores[cid], reverse=True)
        if not ranked:
            return []

        candidates = await self._fetch_chunks(
            ranked,
            tenant_id,
            scores,
            user_id,
            department_ids,
        )
        return self._document_diverse_top_k(candidates, top_k)

    @staticmethod
    def _document_diverse_top_k(candidates: list[Chunk], top_k: int) -> list[Chunk]:
        """Select a stable Top-K while softly limiting document monopolies.

        The first pass keeps at most two chunks per document.  Two preserves
        useful neighbouring evidence for fact questions, while freeing room
        for additional documents in list and relational questions.  If the
        candidate pool has too few distinct documents, deferred chunks are
        appended in their original rank order, so this is a soft diversity
        preference rather than a hard loss of context.
        """
        if top_k <= 0:
            return []

        selected: list[Chunk] = []
        deferred: list[Chunk] = []
        document_counts: dict[UUID, int] = {}
        for chunk in candidates:
            count = document_counts.get(chunk.document_id, 0)
            if count < _PREFERRED_CHUNKS_PER_DOCUMENT:
                selected.append(chunk)
                document_counts[chunk.document_id] = count + 1
                if len(selected) == top_k:
                    return selected
            else:
                deferred.append(chunk)

        remaining = top_k - len(selected)
        if remaining > 0:
            selected.extend(deferred[:remaining])
        return selected

    async def _bm25_search(
        self,
        query: str,
        tenant_id: UUID,
        limit: int,
        *,
        user_id: UUID | None = None,
        department_ids: list[UUID] | None = None,
    ) -> list[dict[str, Any]]:
        """Run deterministic ILIKE lexical retrieval with visibility pre-filter.

        The complete query remains the primary lexical term. Structured
        business identifiers receive an additional exact substring branch
        across chunk content and document identity fields, so a query such as
        ``PRD-001 的价格`` can retrieve ``KB-PRD-001`` without depending on
        semantic similarity or an LLM rewrite.
        """
        normalized_query = query.strip()
        if not normalized_query:
            return []

        identifiers = extract_structured_identifiers(normalized_query)
        lexical_terms = [normalized_query]
        lexical_terms.extend(
            identifier
            for identifier in identifiers
            if identifier.casefold() != normalized_query.casefold()
        )

        params: list[Any] = []
        match_groups: list[str] = []
        identifier_identity_groups: list[str] = []
        identifier_title_groups: list[str] = []
        identifier_content_groups: list[str] = []
        identifier_keys = {identifier.casefold() for identifier in identifiers}
        for term in lexical_terms:
            param_index = len(params)
            params.append(f"%{term}%")
            group = (
                f"(c.content ILIKE :p{param_index} "
                f"OR d.title ILIKE :p{param_index} "
                f"OR d.source_uri ILIKE :p{param_index} "
                f"OR d.metadata->>'doc_id' ILIKE :p{param_index})"
            )
            match_groups.append(group)
            if term.casefold() in identifier_keys:
                identifier_identity_groups.append(
                    f"(d.source_uri ILIKE :p{param_index} "
                    f"OR d.metadata->>'doc_id' ILIKE :p{param_index})"
                )
                identifier_title_groups.append(f"d.title ILIKE :p{param_index}")
                identifier_content_groups.append(f"c.content ILIKE :p{param_index}")

        visibility_sql = self._append_visibility_filter(
            params,
            user_id=user_id,
            department_ids=department_ids,
            table_alias="c",
        )
        order_sql = ""
        if identifier_identity_groups:
            order_sql = (
                f"CASE WHEN {' OR '.join(identifier_identity_groups)} THEN 0 "
                f"WHEN {' OR '.join(identifier_title_groups)} THEN 1 "
                f"WHEN {' OR '.join(identifier_content_groups)} THEN 2 "
                "ELSE 3 END, "
            )
        limit_param = len(params)
        params.append(limit)
        rows = await self._db.tenant_scoped_fetch(
            "SELECT c.id FROM knowledge.chunks c "
            "JOIN knowledge.documents d "
            "ON d.id = c.document_id AND d.tenant_id = c.tenant_id "
            "WHERE c.tenant_id = :tenant_id "
            f"AND ({' OR '.join(match_groups)}) {visibility_sql}"
            f"ORDER BY {order_sql}length(c.content), c.chunk_index "
            f"LIMIT :p{limit_param}",
            tenant_id,
            *params,
        )
        return rows

    @staticmethod
    def _append_visibility_filter(
        params: list[Any],
        *,
        user_id: UUID | None,
        department_ids: list[UUID] | None,
        table_alias: str,
    ) -> str:
        """Append visibility bind values and return a correctly indexed clause."""
        if user_id is None:
            return ""

        prefix = f"{table_alias}." if table_alias else ""
        user_param = len(params)
        params.append(user_id)
        parts = [
            f"{prefix}scope = 'enterprise'",
            f"({prefix}scope = 'personal' AND {prefix}owner_id = :p{user_param})",
        ]
        department_params: list[str] = []
        for department_id in department_ids or []:
            department_params.append(f":p{len(params)}")
            params.append(department_id)
        if department_params:
            parts.append(
                f"({prefix}scope = 'department' "
                f"AND {prefix}owner_id IN ({', '.join(department_params)}))"
            )
        return f"AND ({' OR '.join(parts)}) "

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
        params: list[Any] = [*ids, tenant_id]
        tenant_param_idx = len(ids)

        # C05: Permission-pre-filter at SQL level.
        # When user_id is provided, only return visible chunks:
        # - scope = 'enterprise' (visible to all)
        # - scope = 'personal' AND owner_id = user_id
        # - scope = 'department' AND owner_id IN user's departments
        scope_filter = self._append_visibility_filter(
            params,
            user_id=user_id,
            department_ids=department_ids,
            table_alias="",
        )

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
            meta = dict(row.get("metadata") or {})
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
