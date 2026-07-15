"""KnowledgeConnector — read-only connector for knowledge schema tables.

Exposes knowledge.documents / chunks / ontologies / ontology_nodes /
org_memories to the BI data browser so admins can inspect knowledge base
contents (chunks, embeddings metadata, ontology nodes) without writing SQL.

Read-only by design: writes go through dedicated /admin/knowledge/* endpoints.
The ``embedding`` vector column on ``chunks`` and ``org_memories`` is stripped
from read results and schema descriptions (1024-dim vectors are not useful in
a UI table and bloat JSON responses).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eaos.data.connector import (
    DataResource,
    DataResult,
    ReadQuery,
    SchemaDescription,
    WriteOperation,
    WriteResult,
)

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient


_RESOURCES: list[DataResource] = [
    DataResource(
        name="documents",
        display_name="文档",
        description="知识库文档主表（title/source_type/status）",
        access_mode="read",
    ),
    DataResource(
        name="chunks",
        display_name="文档分块",
        description="文档切分后的向量分块（含 token_count/metadata，不含 embedding）",
        access_mode="read",
    ),
    DataResource(
        name="ontologies",
        display_name="本体",
        description="本体版本表（name/version/status）",
        access_mode="read",
    ),
    DataResource(
        name="ontology_nodes",
        display_name="本体节点",
        description="本体节点（node_type/name/parent_id/properties）",
        access_mode="read",
    ),
    DataResource(
        name="org_memories",
        display_name="组织记忆",
        description="组织记忆条目（scope/memory_type/confidence，不含 embedding）",
        access_mode="read",
    ),
]

_ALLOWED_RESOURCES: set[str] = {r.name for r in _RESOURCES}

# Tables with a vector ``embedding`` column that must be stripped from results.
_VECTOR_TABLES: set[str] = {"chunks", "org_memories"}

# Columns allowed in WHERE filters (tenant_id is always implicit).
_ALLOWED_FILTER_COLUMNS: dict[str, set[str]] = {
    "documents": {
        "source_type",
        "status",
        "title",
        "version",
        "content_hash",
    },
    "chunks": {"document_id", "chunk_index", "token_count"},
    "ontologies": {"name", "version", "status"},
    "ontology_nodes": {"ontology_id", "node_type", "name", "parent_id"},
    "org_memories": {"scope", "memory_type", "owner_id", "source"},
}


class KnowledgeConnector:
    """Read-only DataConnector for the ``knowledge`` schema."""

    SCHEMA = "knowledge"
    _ALLOWED_RESOURCES = _ALLOWED_RESOURCES

    def __init__(self, db: DbClient) -> None:
        self._db = db

    async def list_resources(self, tenant_id: UUID) -> list[DataResource]:
        del tenant_id  # resource catalog is shared; row-level isolation in read
        return list(_RESOURCES)

    async def read(
        self,
        tenant_id: UUID,
        resource: str,
        query: ReadQuery,
    ) -> DataResult:
        if resource not in self._ALLOWED_RESOURCES:
            return DataResult(rows=[], total=0)

        # Strip the embedding vector column — it is 1024 floats and not useful
        # in a data browser UI. If the caller explicitly requests fields, we
        # still filter out ``embedding`` to avoid accidental large payloads.
        if query.fields:
            fields_list = [f for f in query.fields if f != "embedding"]
        elif resource in _VECTOR_TABLES:
            # Build "all columns except embedding" by listing at read time.
            # We do this with a negation: select all columns via information_schema.
            # Simpler: hardcode the safe column list per vector table.
            if resource == "chunks":
                fields_list = [
                    "id",
                    "document_id",
                    "tenant_id",
                    "chunk_index",
                    "content",
                    "token_count",
                    "metadata",
                    "created_at",
                ]
            else:  # org_memories
                fields_list = [
                    "id",
                    "tenant_id",
                    "scope",
                    "owner_id",
                    "memory_type",
                    "content",
                    "confidence",
                    "source",
                    "created_at",
                    "last_accessed",
                    "access_count",
                ]
        else:
            fields_list = ["*"]

        fields_sql = ", ".join(fields_list)
        sql = f"SELECT {fields_sql} FROM {self.SCHEMA}.{resource}"
        params: list[Any] = [tenant_id]
        where_clauses: list[str] = ["tenant_id = :p0"]
        allowed_filters = _ALLOWED_FILTER_COLUMNS.get(resource, set())
        for col, val in query.filters.items():
            if col not in allowed_filters:
                continue
            idx = len(params)
            where_clauses.append(f"{col} = :p{idx}")
            params.append(val)
        sql += " WHERE " + " AND ".join(where_clauses)
        if query.order_by:
            order_parts = [f"{col} {direction}" for col, direction in query.order_by]
            sql += " ORDER BY " + ", ".join(order_parts)
        sql += f" LIMIT :p{len(params)} OFFSET :p{len(params) + 1}"
        params.extend([query.limit, query.offset])
        rows = await self._db.fetch(sql, *params)

        count_sql = (
            f"SELECT count(*) AS total FROM {self.SCHEMA}.{resource} "
            f"WHERE tenant_id = :p0"
        )
        count_params: list[Any] = [tenant_id]
        for col, val in query.filters.items():
            if col not in allowed_filters:
                continue
            idx = len(count_params)
            count_sql += f" AND {col} = :p{idx}"
            count_params.append(val)
        count_row = await self._db.fetch_one(count_sql, *count_params)
        total = int(count_row["total"]) if count_row else 0
        return DataResult(rows=rows, total=total)

    async def write(
        self,
        tenant_id: UUID,
        resource: str,
        operation: WriteOperation,
    ) -> WriteResult:
        del tenant_id, resource, operation
        return WriteResult(
            success=False,
            error=(
                "knowledge schema is read-only via BI connector; "
                "use /admin/knowledge/documents or /admin/ontology/terms"
            ),
        )

    async def describe_schema(
        self,
        tenant_id: UUID,
        resource: str,
    ) -> SchemaDescription:
        rows = await self._db.fetch(
            "SELECT column_name, data_type, is_nullable, "
            "col_description((table_schema||'.'||table_name)::regclass, "
            "ordinal_position) AS comment "
            "FROM information_schema.columns "
            "WHERE table_schema = :p0 AND table_name = :p1 "
            "ORDER BY ordinal_position",
            self.SCHEMA,
            resource,
        )
        # Hide the embedding vector column — it is not actionable in the UI.
        visible_cols = [r["column_name"] for r in rows if r["column_name"] != "embedding"]
        columns = [
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "nullable": r["is_nullable"] == "YES",
                "comment": r.get("comment"),
            }
            for r in rows
            if r["column_name"] != "embedding"
        ]
        cols_sql = ", ".join(visible_cols)
        sample_rows = await self._db.fetch(
            f"SELECT {cols_sql} FROM {self.SCHEMA}.{resource} "
            f"WHERE tenant_id = :p0 LIMIT 3",
            tenant_id,
        )
        return SchemaDescription(
            table_name=f"{self.SCHEMA}.{resource}",
            columns=columns,
            relations=[],
            sample_rows=sample_rows,
        )

    async def rollback(self, tenant_id: UUID, snapshot: dict[str, Any]) -> None:
        del tenant_id, snapshot  # read-only connector — nothing to roll back
