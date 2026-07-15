"""KnowledgeEngine — unified facade over ontology + RAG + memory.

Agent code calls this single interface; it internally orchestrates query
rewriting, RAG retrieval, memory recall, and merges results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient
    from eaos.knowledge.memory.consolidator import MemoryConsolidator
    from eaos.knowledge.memory.store import Memory, MemoryScope, MemoryStore
    from eaos.knowledge.ontology.query_rewrite import QueryRewriter, RewrittenQuery
    from eaos.knowledge.ontology.repository import OntologyRepository
    from eaos.knowledge.rag.pipeline import Document, RAGPipeline


@dataclass(frozen=True)
class SearchResult:
    """A unified search result from any knowledge source."""

    content: str
    score: float
    source: str  # rag/memory/ontology
    metadata: dict[str, Any]


class KnowledgeEngine(Protocol):
    """Unified knowledge engine facade.

    Combines: ontology-driven query rewriting + RAG retrieval + organizational
    memory recall. The agent calls this; it doesn't need to know which
    subsystem produced each result.
    """

    async def search(
        self,
        query: str,
        tenant_id: UUID,
        top_k: int = 5,
        user_id: UUID | None = None,
    ) -> list[SearchResult]:
        """Comprehensive search: rewrite query -> RAG + memory -> merge.

        When ``user_id`` is provided, RAG results are filtered by three-tier
        scope visibility (personal + department + enterprise).
        """
        ...

    async def ingest_document(
        self,
        document: Document,
        tenant_id: UUID,
    ) -> list[UUID]:
        """Ingest a document into RAG."""
        ...

    async def recall_memory(
        self,
        query: str,
        tenant_id: UUID,
        scope: MemoryScope,
        owner_id: UUID | None,
        top_k: int = 5,
    ) -> list[Memory]:
        """Recall organizational memories."""
        ...

    async def rewrite_query(
        self,
        query: str,
        tenant_id: UUID,
    ) -> RewrittenQuery:
        """Ontology-driven query rewrite (for Text2SQL and RAG)."""
        ...


class KnowledgeEngineImpl:
    """KnowledgeEngine facade orchestrating rewriter + RAG + memory."""

    def __init__(
        self,
        ontology_repo: OntologyRepository,
        rewriter: QueryRewriter,
        rag: RAGPipeline,
        memory_store: MemoryStore,
        consolidator: MemoryConsolidator,
        db: DbClient | None = None,
    ) -> None:
        self._ontology_repo = ontology_repo
        self._rewriter = rewriter
        self._rag = rag
        self._memory_store = memory_store
        self._consolidator = consolidator
        self._db = db

    async def search(
        self,
        query: str,
        tenant_id: UUID,
        top_k: int = 5,
        user_id: UUID | None = None,
    ) -> list[SearchResult]:
        rewritten = await self._rewriter.rewrite(query, tenant_id)
        chunks = await self._rag.retrieve(rewritten.rewritten, tenant_id, top_k=top_k)

        # When user_id is provided, post-filter chunks by three-tier scope visibility.
        # Chunks visible to the user: enterprise (all) + personal (own) + department (own depts).
        scope_map: dict[Any, tuple[str, Any]] = {}
        if user_id is not None and self._db is not None and chunks:
            scope_map = await self._query_chunk_scopes(chunks, tenant_id, user_id)
            chunks = self._filter_visible_chunks(chunks, scope_map, user_id)

        results: list[SearchResult] = []
        for chunk in chunks:
            meta = dict(chunk.metadata)
            if hasattr(chunk, "id") and chunk.id in scope_map:
                scope, owner_id = scope_map[chunk.id]
                meta["scope"] = scope
                if owner_id is not None:
                    meta["owner_id"] = str(owner_id)
            results.append(
                SearchResult(
                    content=chunk.content,
                    score=1.0,
                    source="rag",
                    metadata=meta,
                )
            )
        return results

    async def _query_chunk_scopes(
        self,
        chunks: list[Any],
        tenant_id: UUID,
        user_id: UUID,
    ) -> dict[Any, tuple[str, Any]]:
        """Query scope/owner_id for each chunk and return a visibility map."""
        chunk_ids = [c.id for c in chunks if hasattr(c, "id")]
        if not chunk_ids:
            return {}

        placeholders = ", ".join(f":p{i}" for i in range(len(chunk_ids)))
        params: list[Any] = list(chunk_ids)
        params.append(tenant_id)
        rows = await self._db.fetch(  # type: ignore[union-attr]
            f"SELECT id, scope, owner_id FROM knowledge.chunks "
            f"WHERE id IN ({placeholders}) AND tenant_id = :p{len(chunk_ids)}",
            *params,
        )

        scope_map: dict[Any, tuple[str, Any]] = {
            row["id"]: (row.get("scope", "enterprise"), row.get("owner_id"))
            for row in rows
        }

        # Query user's department IDs for department-scope visibility.
        dept_rows = await self._db.fetch(  # type: ignore[union-attr]
            "SELECT department_id FROM iam.memberships WHERE user_id = :p0",
            user_id,
        )
        self._user_dept_ids = {row["department_id"] for row in dept_rows}
        return scope_map

    def _filter_visible_chunks(
        self,
        chunks: list[Any],
        scope_map: dict[Any, tuple[str, Any]],
        user_id: UUID,
    ) -> list[Any]:
        """Keep only chunks the user can see based on scope visibility."""
        user_dept_ids = getattr(self, "_user_dept_ids", set())
        visible: list[Any] = []
        for chunk in chunks:
            scope, owner_id = scope_map.get(
                getattr(chunk, "id", None), ("enterprise", None)
            )
            if scope == "enterprise":
                visible.append(chunk)
            elif scope == "personal" and owner_id == user_id:
                visible.append(chunk)
            elif scope == "department" and owner_id in user_dept_ids:
                visible.append(chunk)
        return visible

    async def ingest_document(
        self,
        document: Document,
        tenant_id: UUID,
    ) -> list[UUID]:
        return await self._rag.ingest(document, tenant_id)

    async def recall_memory(
        self,
        query: str,
        tenant_id: UUID,
        scope: MemoryScope,
        owner_id: UUID | None,
        top_k: int = 5,
    ) -> list[Memory]:
        return await self._memory_store.recall(query, tenant_id, scope, owner_id, top_k)

    async def rewrite_query(
        self,
        query: str,
        tenant_id: UUID,
    ) -> RewrittenQuery:
        return await self._rewriter.rewrite(query, tenant_id)
