"""PgVectorStore: pgvector-backed VectorStore implementation.

Depends on DbClient for SQL execution. Table names are whitelisted to prevent
SQL injection (the table name is interpolated into SQL, not parameterized).
Filter and update column names are validated against an identifier pattern
before interpolation.

Parameter convention follows DbClient: positional ``*params`` mapped to
``:p0, :p1, ...`` named binds. search() uses tenant_scoped_fetch which injects
``:tenant_id`` automatically; other methods pass tenant_id positionally.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from eaos.core.errors import DataError
from eaos.infra.vector.base import VectorSearchResult

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient


_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class PgVectorStore:
    """VectorStore backed by pgvector via DbClient."""

    _ALLOWED_TABLES: frozenset[str] = frozenset(
        {"knowledge.chunks", "knowledge.org_memories"}
    )

    def __init__(self, db: DbClient) -> None:
        self._db = db

    def _check_table(self, table: str) -> None:
        if table not in self._ALLOWED_TABLES:
            raise DataError(f"table not allowed: {table}")

    @staticmethod
    def _validate_key(key: str, *, kind: str) -> None:
        if not _IDENT_RE.match(key):
            raise DataError(f"invalid {kind} key: {key!r}")

    async def search(
        self,
        embedding: list[float],
        tenant_id: UUID,
        table: str,
        top_k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        self._check_table(table)
        params: list[Any] = [str(embedding)]
        sql = (
            f"SELECT id, embedding <=> CAST(:p0 AS vector) AS score "
            f"FROM {table} WHERE tenant_id = :tenant_id AND embedding IS NOT NULL"
        )
        if filter:
            for k, v in filter.items():
                self._validate_key(k, kind="filter")
                sql += f" AND {k} = :p{len(params)}"
                params.append(v)
        sql += f" ORDER BY score LIMIT :p{len(params)}"
        params.append(top_k)
        rows = await self._db.tenant_scoped_fetch(sql, tenant_id, *params)
        return [
            VectorSearchResult(
                id=row["id"],
                content="",
                score=float(row["score"]) if row["score"] is not None else 0.0,
                metadata={},
            )
            for row in rows
        ]

    async def insert(
        self,
        table: str,
        items: list[dict[str, Any]],
        tenant_id: UUID,
    ) -> None:
        self._check_table(table)
        if not items:
            return
        full_items = [{**item, "tenant_id": tenant_id} for item in items]
        columns = list(full_items[0].keys())
        for col in columns:
            self._validate_key(col, kind="column")
        col_list = ", ".join(columns)
        placeholders = []
        for i, col in enumerate(columns):
            if col == "embedding":
                placeholders.append(f"CAST(:p{i} AS vector)")
            else:
                placeholders.append(f":p{i}")
        ph_str = ", ".join(placeholders)
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({ph_str})"
        params_list = [
            tuple(
                str(item[c]) if c == "embedding" and item[c] is not None else item[c]
                for c in columns
            )
            for item in full_items
        ]
        await self._db.execute_many(sql, params_list)

    async def delete(self, table: str, ids: list[UUID], tenant_id: UUID) -> None:
        self._check_table(table)
        if not ids:
            return
        placeholders = ", ".join(f":p{i}" for i in range(len(ids)))
        sql = (
            f"DELETE FROM {table} WHERE id IN ({placeholders}) "
            f"AND tenant_id = :p{len(ids)}"
        )
        params: list[Any] = list(ids) + [tenant_id]
        await self._db.execute(sql, *params)

    async def update(
        self,
        table: str,
        item_id: UUID,
        updates: dict[str, Any],
        tenant_id: UUID,
    ) -> None:
        self._check_table(table)
        if not updates:
            return
        set_parts: list[str] = []
        params: list[Any] = []
        for k, v in updates.items():
            self._validate_key(k, kind="update")
            set_parts.append(f"{k} = :p{len(params)}")
            params.append(v)
        set_clause = ", ".join(set_parts)
        sql = (
            f"UPDATE {table} SET {set_clause} "
            f"WHERE id = :p{len(params)} AND tenant_id = :p{len(params) + 1}"
        )
        params.extend([item_id, tenant_id])
        await self._db.execute(sql, *params)
