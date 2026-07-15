"""Data connector protocol — unified interface to enterprise data sources.

Each connector (ERP, CRM, database) implements this. MCP servers wrap connectors
to expose them as tools to agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True)
class DataResource:
    """A queryable resource (table, object, view) in a data source."""

    name: str  # table/object name
    display_name: str  # Chinese display name
    description: str
    access_mode: str  # read / read_write


@dataclass(frozen=True)
class ReadQuery:
    """A read query against a data resource."""

    filters: dict[str, object] = field(default_factory=dict)
    fields: list[str] | None = None  # None = all fields
    limit: int = 100
    offset: int = 0
    order_by: list[tuple[str, str]] | None = None  # [(field, "asc"|"desc")]


@dataclass(frozen=True)
class WriteOperation:
    """A write operation against a data resource."""

    operation: str  # create/update/delete
    record_id: str | None = None  # None for create
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataResult:
    """Result of a read query."""

    rows: list[dict[str, Any]]
    total: int


@dataclass(frozen=True)
class WriteResult:
    """Result of a write operation."""

    success: bool
    before: dict[str, Any] | None = None  # snapshot for rollback
    after: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class SchemaDescription:
    """Schema metadata for Text2SQL prompt construction."""

    table_name: str
    columns: list[dict[str, Any]]  # [{name, type, nullable, comment}]
    relations: list[dict[str, Any]]  # [{from_table, from_col, to_table, to_col}]
    sample_rows: list[dict[str, Any]] = field(default_factory=list)  # few-shot examples


class DataConnector(Protocol):
    """Enterprise data connector unified interface.

    Implementations: ERPConnector, CRMConnector, DatabaseConnector.
    All methods are tenant-scoped via tenant_id parameter.
    """

    async def list_resources(self, tenant_id: UUID) -> list[DataResource]:
        """List queryable resources for this tenant."""
        ...

    async def read(
        self,
        tenant_id: UUID,
        resource: str,
        query: ReadQuery,
    ) -> DataResult:
        """Read data from a resource (always read-only safe)."""
        ...

    async def write(
        self,
        tenant_id: UUID,
        resource: str,
        operation: WriteOperation,
    ) -> WriteResult:
        """Write data to a resource.

        MUST be wrapped by Harness @guarded(action="write_data") — high-risk
        operations require human-in-the-loop confirmation and rollback snapshot.
        """
        ...

    async def describe_schema(
        self,
        tenant_id: UUID,
        resource: str,
    ) -> SchemaDescription:
        """Describe resource schema for Text2SQL and ontology mapping."""
        ...

    async def rollback(self, tenant_id: UUID, snapshot: dict[str, Any]) -> None:
        """Rollback a previously snapshotted write (Harness compliance)."""
        ...
