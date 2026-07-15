"""Database client protocol.

PostgreSQL is the primary store. All business tables carry tenant_id for
row-level isolation; this protocol abstracts the connection so business code
does not import SQLAlchemy directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager
    from uuid import UUID


class DbClient(Protocol):
    """Async PostgreSQL client.

    Concrete implementations (e.g. PgClient) provide ``session()`` as an
    ``@asynccontextmanager`` so callers use ``async with client.session() as s:``
    to get a transactional scope that commits on clean exit and rolls back on
    exception.
    """

    def session(self) -> AbstractAsyncContextManager[Any]:
        """Return an async context manager yielding a session/transaction scope.

        Commits on clean exit, rolls back on exception. Implementations should
        decorate with ``@asynccontextmanager``.
        """
        ...

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        """Fetch multiple rows as list of dicts."""
        ...

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        """Fetch a single row or None."""
        ...

    async def fetch_val(self, sql: str, *params: Any) -> Any:
        """Fetch a single scalar value."""
        ...

    async def execute(self, sql: str, *params: Any) -> None:
        """Execute a statement with no result set."""
        ...

    async def execute_many(self, sql: str, params_list: list[tuple[Any, ...]]) -> None:
        """Execute a statement with many parameter sets (batch)."""
        ...

    async def tenant_scoped_fetch(
        self,
        sql: str,
        tenant_id: UUID,
        *params: Any,
    ) -> list[dict[str, Any]]:
        """Fetch rows scoped to a tenant; injects tenant_id as first param.

        Convenience wrapper that forces tenant filtering. The SQL MUST contain
        a `:tenant_id` or `$1` placeholder for the tenant filter.
        """
        ...
