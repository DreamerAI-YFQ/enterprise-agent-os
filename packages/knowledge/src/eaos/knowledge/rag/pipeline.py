"""RAG pipeline — document ingestion, chunking, hybrid retrieval, reranking."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient
    from eaos.infra.vector.base import Embedder, VectorStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Document:
    """A source document for RAG ingestion."""

    source_type: str  # pdf/word/confluence/email/web
    source_uri: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    scope: str = "enterprise"  # personal/department/enterprise
    owner_id: UUID | None = None  # user_id (personal) or dept_id (department)


@dataclass(frozen=True)
class Chunk:
    """A document chunk with optional embedding."""

    id: UUID
    document_id: UUID
    tenant_id: UUID
    chunk_index: int
    content: str
    token_count: int
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)  # parent chunk, page, type
    score: float = 0.0  # C05: retrieval score (RRF or vector similarity)


class Chunker(Protocol):
    """Smart document chunker preserving semantic boundaries."""

    async def chunk(self, document: Document) -> list[Chunk]:
        """Split document into semantically coherent chunks."""
        ...


class Retriever(Protocol):
    """Hybrid retriever: vector + BM25, fused via RRF."""

    async def retrieve(
        self,
        query: str,
        tenant_id: UUID,
        top_k: int = 10,
    ) -> list[Chunk]:
        """Retrieve top-k relevant chunks via hybrid search."""
        ...


class Reranker(Protocol):
    """Re-ranker for improving retrieval precision."""

    async def rerank(
        self,
        query: str,
        chunks: list[Chunk],
        top_k: int = 5,
    ) -> list[Chunk]:
        """Re-order chunks by relevance to the query."""
        ...


class RAGPipeline(Protocol):
    """End-to-end RAG: ingest, retrieve, rerank."""

    async def ingest(self, document: Document, tenant_id: UUID) -> list[UUID]:
        """Ingest a document: parse -> chunk -> embed -> store. Returns chunk ids."""
        ...

    async def retrieve(
        self,
        query: str,
        tenant_id: UUID,
        top_k: int = 5,
        scope_filter: dict[str, Any] | None = None,
        *,
        user_id: UUID | None = None,
        department_ids: list[UUID] | None = None,
    ) -> list[Chunk]:
        """Retrieve relevant chunks: rewrite query -> hybrid search -> rerank.

        ``scope_filter`` narrows results by scope visibility. When None, returns
        all scopes (backward-compatible with pre-scope callers).
        ``user_id`` and ``department_ids`` apply permission visibility before
        vector and keyword Top-K selection.
        """
        ...

    async def delete_document(self, document_id: UUID, tenant_id: UUID) -> None:
        """Delete a document and all its chunks."""
        ...


class RAGPipelineImpl:
    """End-to-end RAG pipeline: chunk → embed → store, retrieve → rerank."""

    def __init__(
        self,
        chunker: Chunker,
        retriever: Retriever,
        reranker: Reranker,
        embedder: Embedder,
        vector_store: VectorStore,
        db: DbClient,
    ) -> None:
        self._chunker = chunker
        self._retriever = retriever
        self._reranker = reranker
        self._embedder = embedder
        self._vs = vector_store
        self._db = db

    async def ingest(self, document: Document, tenant_id: UUID) -> list[UUID]:
        chunks = await self._chunker.chunk(document)
        if not chunks:
            return []
        content_hash = hashlib.sha256(document.content.encode()).hexdigest()

        document_row = await self._db.fetch_one(
            "SELECT id, status, scope, owner_id FROM knowledge.documents "
            "WHERE tenant_id = :p0 AND content_hash = :p1 AND version = :p2",
            tenant_id,
            content_hash,
            document.version,
        )
        existing_chunks: list[dict[str, Any]] = []
        if document_row is not None:
            self._validate_existing_visibility(document, document_row)
            existing_chunks = await self._load_document_chunks(document_row["id"], tenant_id)
            if self._chunks_are_complete(chunks, existing_chunks):
                if document_row.get("status") != "indexed":
                    await self._mark_document_status(document_row["id"], tenant_id, "indexed")
                return self._ordered_chunk_ids(existing_chunks)

        # Complete external embedding calls before creating a new document.
        # An embedding timeout therefore leaves no orphaned row. An existing
        # incomplete row remains explicitly retryable.
        embeddings = [await self._embedder.embed(chunk.content) for chunk in chunks]

        if document_row is None:
            preferred_document_id = chunks[0].document_id
            await self._db.execute(
                "INSERT INTO knowledge.documents "
                "(id, tenant_id, source_type, source_uri, title, content_hash, "
                "version, metadata, scope, owner_id, status) "
                "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, "
                "CAST(:p7 AS jsonb), :p8, :p9, 'indexing') "
                "ON CONFLICT (tenant_id, content_hash, version) DO NOTHING",
                preferred_document_id,
                tenant_id,
                document.source_type,
                document.source_uri,
                document.title,
                content_hash,
                document.version,
                _serialize_metadata(document.metadata),
                document.scope,
                document.owner_id,
            )
            document_row = await self._db.fetch_one(
                "SELECT id, status, scope, owner_id FROM knowledge.documents "
                "WHERE tenant_id = :p0 AND content_hash = :p1 AND version = :p2",
                tenant_id,
                content_hash,
                document.version,
            )
            if document_row is None:
                raise RuntimeError("document insert did not produce a recoverable row")
            self._validate_existing_visibility(document, document_row)
            # A concurrent ingest may have won the unique-key race and already
            # completed indexing while this call was embedding.
            existing_chunks = await self._load_document_chunks(document_row["id"], tenant_id)
            if self._chunks_are_complete(chunks, existing_chunks):
                if document_row.get("status") != "indexed":
                    await self._mark_document_status(document_row["id"], tenant_id, "indexed")
                return self._ordered_chunk_ids(existing_chunks)

        document_id = document_row["id"]
        await self._mark_document_status(document_id, tenant_id, "indexing")
        if existing_chunks:
            await self._db.execute(
                "DELETE FROM knowledge.chunks WHERE document_id = :p0 AND tenant_id = :p1",
                document_id,
                tenant_id,
            )

        items: list[dict[str, Any]] = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            items.append(
                {
                    "id": chunk.id,
                    "document_id": document_id,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "token_count": chunk.token_count,
                    "embedding": embedding,
                    "metadata": _serialize_metadata(chunk.metadata),
                    "scope": document.scope,
                    "owner_id": document.owner_id,
                }
            )
        if items:
            await self._vs.insert("knowledge.chunks", items, tenant_id)
        await self._mark_document_status(document_id, tenant_id, "indexed")
        return [c.id for c in chunks]

    async def _load_document_chunks(
        self,
        document_id: UUID,
        tenant_id: UUID,
    ) -> list[dict[str, Any]]:
        return await self._db.fetch(
            "SELECT id, chunk_index, content, "
            "embedding IS NOT NULL AS has_embedding "
            "FROM knowledge.chunks "
            "WHERE document_id = :p0 AND tenant_id = :p1 "
            "ORDER BY chunk_index, id",
            document_id,
            tenant_id,
        )

    @staticmethod
    def _chunks_are_complete(
        expected: list[Chunk],
        existing: list[dict[str, Any]],
    ) -> bool:
        if len(expected) != len(existing):
            return False
        by_index = {int(row["chunk_index"]): row for row in existing}
        if len(by_index) != len(existing):
            return False
        return all(
            (row := by_index.get(chunk.chunk_index)) is not None
            and row.get("content") == chunk.content
            and bool(row.get("has_embedding"))
            for chunk in expected
        )

    @staticmethod
    def _validate_existing_visibility(
        document: Document,
        existing: dict[str, Any],
    ) -> None:
        existing_scope = str(existing.get("scope") or "enterprise")
        existing_owner = existing.get("owner_id")
        if existing_scope != document.scope or existing_owner != document.owner_id:
            raise ValueError(
                "same document content/version already exists with different visibility"
            )

    @staticmethod
    def _ordered_chunk_ids(existing: list[dict[str, Any]]) -> list[UUID]:
        ordered = sorted(
            existing,
            key=lambda item: (item["chunk_index"], str(item["id"])),
        )
        return [row["id"] for row in ordered]

    async def _mark_document_status(
        self,
        document_id: UUID,
        tenant_id: UUID,
        status: str,
    ) -> None:
        await self._db.execute(
            "UPDATE knowledge.documents SET status = :p0 WHERE id = :p1 AND tenant_id = :p2",
            status,
            document_id,
            tenant_id,
        )

    async def retrieve(
        self,
        query: str,
        tenant_id: UUID,
        top_k: int = 5,
        scope_filter: dict[str, Any] | None = None,
        *,
        user_id: UUID | None = None,
        department_ids: list[UUID] | None = None,
    ) -> list[Chunk]:
        """Retrieve relevant chunks: hybrid search -> rerank.

        C05: When user_id is provided, permission filtering is applied
        at the retriever level (pre-fetch), not post-filter.
        """
        try:
            candidates = await self._retriever.retrieve(  # type: ignore[call-arg]
                query,
                tenant_id,
                top_k=top_k * 2,
                user_id=user_id,
                department_ids=department_ids,
            )
        except TypeError:
            # Retriever doesn't accept user_id/department_ids — fall back
            candidates = await self._retriever.retrieve(query, tenant_id, top_k=top_k * 2)

        # Structured identifiers already took the deterministic tenant-scoped
        # lexical path in HybridRetriever. Preserve that exact order and avoid
        # an external LLM reranker that could time out or reintroduce unrelated
        # vector candidates.
        from eaos.knowledge.rag.retriever import extract_structured_identifiers

        if extract_structured_identifiers(query):
            reranked = candidates[:top_k]
        else:
            # C05: Preserve chunk scores through reranking
            try:
                reranked = await self._reranker.rerank(query, candidates, top_k=top_k)
            except Exception:  # noqa: BLE001 - RRF candidates are availability fallback
                logger.warning("reranker failed; preserving RRF candidate order", exc_info=True)
                reranked = candidates[:top_k]
            if candidates and not reranked:
                logger.warning("reranker returned no rows; preserving RRF candidate order")
                reranked = candidates[:top_k]
        # If reranker didn't preserve scores, restore from candidates
        if reranked and not hasattr(reranked[0], "score"):
            return reranked
        score_map = {c.id: c.score for c in candidates if hasattr(c, "score")}
        if score_map:
            return [
                Chunk(
                    id=c.id,
                    document_id=c.document_id,
                    tenant_id=c.tenant_id,
                    chunk_index=c.chunk_index,
                    content=c.content,
                    token_count=c.token_count,
                    metadata=c.metadata,
                    score=score_map.get(c.id, 0.0),
                )
                if hasattr(c, "score") and c.score == 0.0
                else c
                for c in reranked
            ]
        return reranked

    async def delete_document(self, document_id: UUID, tenant_id: UUID) -> None:
        await self._db.execute(
            "DELETE FROM knowledge.documents WHERE id = :p0 AND tenant_id = :p1",
            document_id,
            tenant_id,
        )


def _serialize_metadata(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, ensure_ascii=False)
