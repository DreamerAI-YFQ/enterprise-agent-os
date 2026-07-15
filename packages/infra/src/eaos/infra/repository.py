"""Generic repository protocol.

Most domain entities (agents, skills, memories, traces) follow CRUD patterns.
This protocol provides a uniform interface; concrete repositories extend it
with domain-specific query methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.core.types import PageResult


class Repository(Protocol):
    """Generic CRUD repository protocol.

    Concrete repositories are typed to their entity at implementation time;
    the protocol here defines the shape. Implementations use DbClient.
    """

    async def get(self, id: UUID, tenant_id: UUID) -> object | None:
        """Fetch by id, scoped to tenant. Returns None if not found."""
        ...

    async def list(
        self,
        tenant_id: UUID,
        *,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> PageResult[Any]:
        """Paginated list with optional filters."""
        ...

    async def create(self, entity: object) -> object:
        """Insert a new entity. Entity must carry tenant_id."""
        ...

    async def update(self, id: UUID, tenant_id: UUID, updates: dict[str, Any]) -> object:
        """Partial update by id. Raises NotFoundError if missing."""
        ...

    async def delete(self, id: UUID, tenant_id: UUID) -> None:
        """Delete by id. No-op if missing (idempotent)."""
        ...
