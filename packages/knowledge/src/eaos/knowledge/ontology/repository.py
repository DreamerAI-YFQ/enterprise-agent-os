"""Ontology repository protocol — persistence for ontology nodes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient
    from eaos.knowledge.ontology.model import NodeType, Ontology, OntologyNode


class OntologyRepository(Protocol):
    """Ontology storage and retrieval."""

    async def get(self, tenant_id: UUID, ontology_id: UUID) -> Ontology:
        """Fetch ontology by id."""
        ...

    async def get_active(self, tenant_id: UUID) -> Ontology | None:
        """Fetch the active ontology for a tenant."""
        ...

    async def get_nodes(
        self,
        tenant_id: UUID,
        ontology_id: UUID,
        node_type: NodeType | None = None,
    ) -> list[OntologyNode]:
        """List nodes, optionally filtered by type."""
        ...

    async def create_node(
        self,
        tenant_id: UUID,
        node: OntologyNode,
    ) -> OntologyNode:
        """Create a new node."""
        ...

    async def get_schema_mapping(
        self,
        tenant_id: UUID,
        datasource_id: UUID,
    ) -> dict[str, Any]:
        """Get field-to-ontology mapping for a datasource (Text2SQL use)."""
        ...

    async def search_nodes(
        self,
        tenant_id: UUID,
        query: str,
        top_k: int = 10,
    ) -> list[OntologyNode]:
        """Search nodes by name/properties (for query rewriting)."""
        ...


class PgOntologyRepository:
    """OntologyRepository backed by PostgreSQL via DbClient.

    Parameter convention follows DbClient: tenant-scoped queries use
    ``tenant_scoped_fetch`` (injects ``:tenant_id``); other queries use
    positional ``:p0, :p1, ...`` binds.
    """

    def __init__(self, db: DbClient) -> None:
        self._db = db

    @staticmethod
    def _row_to_ontology(row: dict[str, Any]) -> Ontology:
        from eaos.knowledge.ontology.model import Ontology

        return Ontology(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            version=row.get("version", "1.0.0"),
            status=row.get("status", "active"),
        )

    @staticmethod
    def _row_to_node(row: dict[str, Any]) -> OntologyNode:
        from eaos.knowledge.ontology.model import NodeType, OntologyNode

        props_raw = row.get("properties")
        props: dict[str, Any] = (
            json.loads(props_raw) if isinstance(props_raw, str) else (props_raw or {})
        )
        return OntologyNode(
            id=row["id"],
            ontology_id=row["ontology_id"],
            tenant_id=row["tenant_id"],
            node_type=NodeType(row["node_type"]),
            name=row["name"],
            parent_id=row.get("parent_id"),
            properties=props,
        )

    async def get(self, tenant_id: UUID, ontology_id: UUID) -> Ontology:
        """Fetch ontology by id. Raises NotFoundError if not found."""
        from eaos.core.errors import NotFoundError

        rows = await self._db.tenant_scoped_fetch(
            "SELECT id, tenant_id, name, version, status "
            "FROM knowledge.ontologies "
            "WHERE id = :p0 AND tenant_id = :tenant_id",
            tenant_id,
            ontology_id,
        )
        if not rows:
            raise NotFoundError(f"ontology {ontology_id} not found")
        return self._row_to_ontology(rows[0])

    async def get_active(self, tenant_id: UUID) -> Ontology | None:
        """Fetch the most recently created active ontology for a tenant."""
        rows = await self._db.tenant_scoped_fetch(
            "SELECT id, tenant_id, name, version, status "
            "FROM knowledge.ontologies "
            "WHERE tenant_id = :tenant_id AND status = 'active' "
            "ORDER BY created_at DESC LIMIT 1",
            tenant_id,
        )
        if not rows:
            return None
        return self._row_to_ontology(rows[0])

    async def get_nodes(
        self,
        tenant_id: UUID,
        ontology_id: UUID,
        node_type: NodeType | None = None,
    ) -> list[OntologyNode]:
        """List nodes, optionally filtered by type."""
        if node_type is None:
            rows = await self._db.tenant_scoped_fetch(
                "SELECT id, ontology_id, tenant_id, node_type, name, parent_id, properties "
                "FROM knowledge.ontology_nodes "
                "WHERE ontology_id = :p0 AND tenant_id = :tenant_id "
                "ORDER BY node_type, name",
                tenant_id,
                ontology_id,
            )
        else:
            rows = await self._db.tenant_scoped_fetch(
                "SELECT id, ontology_id, tenant_id, node_type, name, parent_id, properties "
                "FROM knowledge.ontology_nodes "
                "WHERE ontology_id = :p0 AND tenant_id = :tenant_id AND node_type = :p1 "
                "ORDER BY name",
                tenant_id,
                ontology_id,
                str(node_type.value),
            )
        return [self._row_to_node(r) for r in rows]

    async def create_node(
        self,
        tenant_id: UUID,
        node: OntologyNode,
    ) -> OntologyNode:
        """Insert a new node and return the persisted record."""
        rows = await self._db.tenant_scoped_fetch(
            "INSERT INTO knowledge.ontology_nodes "
            "(id, ontology_id, tenant_id, node_type, name, parent_id, properties) "
            "VALUES (:p0, :p1, :tenant_id, :p2, :p3, :p4, CAST(:p5 AS jsonb)) "
            "RETURNING id, ontology_id, tenant_id, node_type, name, parent_id, properties",
            tenant_id,
            node.id,
            node.ontology_id,
            str(node.node_type.value),
            node.name,
            node.parent_id,
            json.dumps(node.properties, ensure_ascii=False),
        )
        return self._row_to_node(rows[0])

    async def get_schema_mapping(
        self,
        tenant_id: UUID,
        datasource_id: UUID,
    ) -> dict[str, Any]:
        """Build a {table: {column: {chinese_name, type}}} mapping from Attribute nodes.

        Used by Text2SQL to annotate schema with Chinese field descriptions.
        """
        # Fetch all object nodes (to get table names) and attribute nodes (columns).
        object_rows = await self._db.tenant_scoped_fetch(
            "SELECT id, name, properties "
            "FROM knowledge.ontology_nodes "
            "WHERE tenant_id = :tenant_id AND node_type = 'object'",
            tenant_id,
        )
        attr_rows = await self._db.tenant_scoped_fetch(
            "SELECT id, parent_id, name, properties "
            "FROM knowledge.ontology_nodes "
            "WHERE tenant_id = :tenant_id AND node_type = 'attribute'",
            tenant_id,
        )

        # Build parent_id -> table_name mapping from object nodes.
        table_by_parent: dict[Any, str] = {}
        for row in object_rows:
            props_raw = row.get("properties")
            props: dict[str, Any] = (
                json.loads(props_raw) if isinstance(props_raw, str) else (props_raw or {})
            )
            table = props.get("table")
            if table:
                table_by_parent[row["id"]] = table

        # Build the mapping: {table: {column: {chinese_name, type}}}.
        mapping: dict[str, Any] = {}
        for row in attr_rows:
            parent_id = row.get("parent_id")
            if parent_id is None:
                continue
            table = table_by_parent.get(parent_id)
            if table is None:
                continue
            props_raw = row.get("properties")
            props = (
                json.loads(props_raw) if isinstance(props_raw, str) else (props_raw or {})
            )
            column = props.get("column")
            if column is None:
                continue
            mapping.setdefault(table, {})[column] = {
                "chinese_name": props.get("chinese_name", column),
                "type": props.get("type", "varchar"),
            }
        return mapping

    async def search_nodes(
        self,
        tenant_id: UUID,
        query: str,
        top_k: int = 10,
    ) -> list[OntologyNode]:
        """Keyword search on node name and properties (ILIKE)."""
        pattern = f"%{query}%"
        rows = await self._db.tenant_scoped_fetch(
            "SELECT id, ontology_id, tenant_id, node_type, name, parent_id, properties "
            "FROM knowledge.ontology_nodes "
            "WHERE tenant_id = :tenant_id "
            "AND (name ILIKE :p0 OR properties::text ILIKE :p1) "
            "ORDER BY node_type LIMIT :p2",
            tenant_id,
            pattern,
            pattern,
            top_k,
        )
        return [self._row_to_node(r) for r in rows]
