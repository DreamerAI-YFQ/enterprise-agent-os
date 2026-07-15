"""SQL sandbox — read-only enforcement and write execution with row/time limits.

Phase 7 T7: ``execute_readonly`` enforces ``SET TRANSACTION READ ONLY`` so
PostgreSQL rejects any INSERT/UPDATE/DELETE at the engine level (fixes gap #6).
``execute_write`` provides an explicit write path for the WritePipeline.
The legacy ``execute`` method is retained for backwards compatibility with
the Text2SQL engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.sql import text

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient


@dataclass(frozen=True)
class SandboxOptions:
    """Sandbox execution options."""

    max_rows: int = 1000
    timeout_sec: int = 30
    read_only: bool = True


class SqlSandbox(Protocol):
    """SQL sandbox — executes validated SQL safely."""

    async def execute(
        self,
        sql: str,
        tenant_id: UUID,
        datasource_id: UUID,
        options: SandboxOptions | None = None,
    ) -> list[dict[str, Any]]:
        """Execute validated SQL in a constrained sandbox (legacy path)."""
        ...

    async def execute_readonly(
        self,
        sql: str,
        params: list[Any],
        tenant_id: UUID,
        options: SandboxOptions | None = None,
    ) -> list[dict[str, Any]]:
        """Execute SQL in a READ ONLY transaction — PG enforces no writes."""
        ...

    async def execute_write(
        self,
        sql: str,
        params: list[Any],
        tenant_id: UUID,
    ) -> None:
        """Execute a write statement (INSERT/UPDATE/DELETE) in a transaction."""
        ...


class PgSqlSandbox:
    """SqlSandbox backed by PostgreSQL via DbClient.

    ``execute_readonly`` uses ``SET TRANSACTION READ ONLY`` for engine-level
    enforcement. ``execute_write`` is the explicit write path. The legacy
    ``execute`` method wraps a simple fetch with a statement timeout.
    """

    def __init__(self, db: DbClient) -> None:
        self._db = db

    @staticmethod
    def _bind_params(params: list[Any]) -> dict[str, Any]:
        """Map positional params to :p0, :p1, ... named binds."""
        return {f"p{i}": value for i, value in enumerate(params)}

    async def execute(
        self,
        sql: str,
        tenant_id: UUID,
        datasource_id: UUID,
        options: SandboxOptions | None = None,
    ) -> list[dict[str, Any]]:
        """Legacy read path — statement timeout + fetch, errors swallowed."""
        del tenant_id, datasource_id  # sandbox is datasource-agnostic
        opts = options or SandboxOptions()
        try:
            await self._db.execute(
                f"SET LOCAL statement_timeout = '{opts.timeout_sec}s'"
            )
            rows = await self._db.fetch(sql)
        except Exception:
            return []
        if len(rows) > opts.max_rows:
            return rows[: opts.max_rows]
        return rows

    async def execute_readonly(
        self,
        sql: str,
        params: list[Any],
        tenant_id: UUID,
        options: SandboxOptions | None = None,
    ) -> list[dict[str, Any]]:
        """Execute SQL in a READ ONLY transaction.

        PostgreSQL rejects INSERT/UPDATE/DELETE at the engine level — this
        cannot be bypassed by SQL syntax tricks. Errors are swallowed and an
        empty list is returned so the Text2SQL retry loop is not broken.
        """
        del tenant_id  # RLS context set by caller via session if needed
        opts = options or SandboxOptions()
        binds = self._bind_params(params)
        try:
            async with self._db.session() as session:
                await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.execute(
                    text(f"SET LOCAL statement_timeout = '{opts.timeout_sec}s'")
                )
                result = await session.execute(text(sql), binds)
                rows = [dict(row) for row in result.mappings().all()]
        except Exception:
            return []
        if len(rows) > opts.max_rows:
            return rows[: opts.max_rows]
        return rows

    async def execute_write(
        self,
        sql: str,
        params: list[Any],
        tenant_id: UUID,
    ) -> None:
        """Execute a write statement in a transactional session.

        The session commits on clean exit, rolls back on exception. Caller
        (WritePipeline) is responsible for validation and audit logging.
        """
        del tenant_id  # RLS context set by caller if needed
        binds = self._bind_params(params)
        async with self._db.session() as session:
            await session.execute(text(sql), binds)
