"""Vector store and embedder protocols.

pgvector is the default implementation. The protocol allows swapping in Milvus
or Qdrant later without touching business code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True)
class VectorSearchResult:
    """A single vector search hit."""

    id: UUID
    content: str
    score: float  # cosine distance (lower = more similar)
    metadata: dict[str, Any]


class VectorStore(Protocol):
    """Vector storage and retrieval protocol."""

    async def search(
        self,
        embedding: list[float],
        tenant_id: UUID,
        table: str,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
        *,
        visibility_user_id: UUID | None = None,
        visibility_department_ids: list[UUID] | None = None,
    ) -> list[VectorSearchResult]:
        """Find nearest neighbors by embedding, scoped to tenant.

        filter is applied as additional WHERE clauses on metadata columns.
        When ``visibility_user_id`` is provided, implementations supporting
        scoped knowledge rows must restrict candidates to enterprise rows,
        the user's personal rows, and rows owned by one of the supplied
        departments before applying Top-K.
        """
        ...

    async def insert(
        self,
        table: str,
        items: list[dict[str, Any]],
        tenant_id: UUID,
    ) -> None:
        """Insert items with embeddings. Each item must have an 'embedding' key."""
        ...

    async def delete(self, table: str, ids: list[UUID], tenant_id: UUID) -> None:
        """Delete vectors by id, scoped to tenant."""
        ...

    async def update(
        self,
        table: str,
        item_id: UUID,
        updates: dict[str, Any],
        tenant_id: UUID,
    ) -> None:
        """Update a vector's metadata or content."""
        ...


class Embedder(Protocol):
    """Text embedding model protocol."""

    async def embed(self, text: str) -> list[float]:
        """Embed a single text."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one call (more efficient)."""
        ...

    @property
    def dimension(self) -> int:
        """Embedding dimension (e.g. 1024 for bge-m3)."""
        ...

    @property
    def model_name(self) -> str:
        """Model identifier for caching/logging."""
        ...
