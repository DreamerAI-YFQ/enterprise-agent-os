"""PostgreSQL adapter implementing DbClient via SQLAlchemy async.

Parameter convention
--------------------
All ``fetch``/``fetch_one``/``fetch_val``/``execute`` methods take positional
``*params``. The SQL string MUST use named placeholders ``:p0, :p1, ...``
(one per positional arg, zero-indexed). This adapter maps the positional
args onto those names before execution. Example::

    await client.fetch("SELECT * FROM iam.users WHERE tenant_id = :p0", tenant_id)
    await client.fetch(
        "SELECT * FROM iam.users WHERE tenant_id = :p0 AND role = :p1",
        tenant_id, "admin",
    )

``tenant_scoped_fetch`` additionally exposes ``:tenant_id`` as a named bind.
Write the SQL as ``WHERE tenant_id = :tenant_id AND ...`` and pass only the
non-tenant params positionally; the adapter injects ``tenant_id`` itself.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from eaos.core.errors import DataError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from eaos.core.config import DatabaseConfig


class PgClient:
    """Async PostgreSQL client backed by SQLAlchemy + asyncpg.

    Implements the DbClient protocol. Sessions are transactional scopes:
    commit on clean exit, rollback on exception.
    """

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._engine: AsyncEngine = create_async_engine(
            config.url,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            echo=config.echo,
        )
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a transactional AsyncSession. Commit on exit, rollback on error."""
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @staticmethod
    def _bind_params(params: tuple[Any, ...]) -> dict[str, Any]:
        """Map positional params to :p0, :p1, ... named binds."""
        return {f"p{i}": value for i, value in enumerate(params)}

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        """Execute SELECT, return rows as list of dicts."""
        try:
            async with self.session() as session:
                result = await session.execute(text(sql), self._bind_params(params))
                return [dict(row) for row in result.mappings().all()]
        except DBAPIError as exc:
            raise DataError(f"fetch failed: {exc}") from exc

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        """Execute SELECT, return first row as dict or None."""
        try:
            async with self.session() as session:
                result = await session.execute(text(sql), self._bind_params(params))
                row = result.mappings().first()
                return dict(row) if row is not None else None
        except DBAPIError as exc:
            raise DataError(f"fetch_one failed: {exc}") from exc

    async def fetch_val(self, sql: str, *params: Any) -> Any:
        """Execute SELECT, return a single scalar value."""
        try:
            async with self.session() as session:
                result = await session.execute(text(sql), self._bind_params(params))
                return result.scalar()
        except DBAPIError as exc:
            raise DataError(f"fetch_val failed: {exc}") from exc

    async def execute(self, sql: str, *params: Any) -> None:
        """Execute a statement with no result set."""
        try:
            async with self.session() as session:
                await session.execute(text(sql), self._bind_params(params))
        except DBAPIError as exc:
            raise DataError(f"execute failed: {exc}") from exc

    async def execute_many(
        self, sql: str, params_list: list[tuple[Any, ...]]
    ) -> None:
        """Execute a statement with many parameter sets (batch)."""
        binds = [self._bind_params(p) for p in params_list]
        try:
            async with self.session() as session:
                await session.execute(text(sql), binds)
        except DBAPIError as exc:
            raise DataError(f"execute_many failed: {exc}") from exc

    async def tenant_scoped_fetch(
        self,
        sql: str,
        tenant_id: UUID,
        *params: Any,
    ) -> list[dict[str, Any]]:
        """Fetch rows scoped to a tenant. SQL must use :tenant_id placeholder.

        Caller does NOT pass tenant_id positionally; the adapter injects it
        into the bind dict under the key ``tenant_id``.
        """
        binds = {"tenant_id": tenant_id}
        binds.update(self._bind_params(params))
        try:
            async with self.session() as session:
                result = await session.execute(text(sql), binds)
                return [dict(row) for row in result.mappings().all()]
        except DBAPIError as exc:
            raise DataError(f"tenant_scoped_fetch failed: {exc}") from exc

    async def close(self) -> None:
        """Dispose the connection pool. Call on application shutdown."""
        await self._engine.dispose()
