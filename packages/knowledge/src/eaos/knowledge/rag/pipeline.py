"""RAG pipeline — document ingestion, chunking, hybrid retrieval, reranking."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient
    from eaos.infra.vector.base import Embedder, VectorStore


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
    ) -> list[Chunk]:
        """Retrieve relevant chunks: rewrite query -> hybrid search -> rerank.

        ``scope_filter`` narrows results by scope visibility. When None, returns
        all scopes (backward-compatible with pre-scope callers).
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
        document_id = chunks[0].document_id
        content_hash = hashlib.sha256(document.content.encode()).hexdigest()

        await self._db.execute(
            "INSERT INTO knowledge.documents "
            "(id, tenant_id, source_type, source_uri, title, content_hash, "
            "version, metadata, scope, owner_id) "
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, CAST(:p7 AS jsonb), :p8, :p9)",
            document_id,
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

        items: list[dict[str, Any]] = []
        for chunk in chunks:
            embedding = await self._embedder.embed(chunk.content)
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
        return [c.id for c in chunks]

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

        # C05: Preserve chunk scores through reranking
        reranked = await self._reranker.rerank(query, candidates, top_k=top_k)
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
                ) if hasattr(c, "score") and c.score == 0.0 else c
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
