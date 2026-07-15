"""Unit tests for PgOntologyRepository.

DbClient is mocked to avoid a live PostgreSQL. Tests verify SQL construction,
parameter binding, result mapping, and search logic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from eaos.core.errors import NotFoundError
from eaos.infra.db.base import DbClient
from eaos.knowledge.ontology.model import NodeType, OntologyNode
from eaos.knowledge.ontology.repository import PgOntologyRepository


def _make_repo() -> tuple[PgOntologyRepository, Any]:
    """Build a PgOntologyRepository with a mocked DbClient."""
    db: Any = MagicMock(spec=DbClient)
    db.tenant_scoped_fetch = AsyncMock()
    repo = PgOntologyRepository(db)
    return repo, db


class TestGet:
    async def test_get_returns_ontology(self) -> None:
        repo, db = _make_repo()
        oid = uuid4()
        tid = uuid4()
        db.tenant_scoped_fetch.return_value = [
            {"id": oid, "tenant_id": tid, "name": "Acme", "version": "1.0", "status": "active"}
        ]
        ont = await repo.get(tid, oid)
        assert ont.id == oid
        assert ont.tenant_id == tid
        assert ont.name == "Acme"

    async def test_get_raises_not_found(self) -> None:
        repo, db = _make_repo()
        db.tenant_scoped_fetch.return_value = []
        with pytest.raises(NotFoundError):
            await repo.get(uuid4(), uuid4())

    async def test_get_uses_correct_sql(self) -> None:
        repo, db = _make_repo()
        oid = uuid4()
        tid = uuid4()
        db.tenant_scoped_fetch.return_value = [
            {"id": oid, "tenant_id": tid, "name": "x", "version": "1", "status": "active"}
        ]
        await repo.get(tid, oid)
        call = db.tenant_scoped_fetch.call_args
        sql = call.args[0]
        assert "knowledge.ontologies" in sql
        assert "id = :p0" in sql
        assert "tenant_id = :tenant_id" in sql
        assert call.args[1] == tid
        assert call.args[2] == oid


class TestGetActive:
    async def test_returns_none_when_no_active(self) -> None:
        repo, db = _make_repo()
        db.tenant_scoped_fetch.return_value = []
        result = await repo.get_active(uuid4())
        assert result is None

    async def test_returns_ontology(self) -> None:
        repo, db = _make_repo()
        tid = uuid4()
        oid = uuid4()
        db.tenant_scoped_fetch.return_value = [
            {"id": oid, "tenant_id": tid, "name": "Active", "version": "2.0", "status": "active"}
        ]
        ont = await repo.get_active(tid)
        assert ont is not None
        assert ont.name == "Active"
        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "status = 'active'" in sql
        assert "ORDER BY created_at DESC" in sql


class TestGetNodes:
    async def test_lists_all_nodes_without_filter(self) -> None:
        repo, db = _make_repo()
        tid = uuid4()
        oid = uuid4()
        node_id = uuid4()
        db.tenant_scoped_fetch.return_value = [
            {
                "id": node_id, "ontology_id": oid, "tenant_id": tid,
                "node_type": "object", "name": "Customer",
                "parent_id": None, "properties": {"table": "erp.customers"},
            }
        ]
        nodes = await repo.get_nodes(tid, oid)
        assert len(nodes) == 1
        assert nodes[0].node_type == NodeType.OBJECT
        assert nodes[0].properties == {"table": "erp.customers"}

    async def test_filters_by_node_type(self) -> None:
        repo, db = _make_repo()
        db.tenant_scoped_fetch.return_value = []
        await repo.get_nodes(uuid4(), uuid4(), node_type=NodeType.ATTRIBUTE)
        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "node_type = :p1" in sql
        params = db.tenant_scoped_fetch.call_args.args[2:]
        assert params[-1] == "attribute"


class TestCreateNode:
    async def test_inserts_and_returns_node(self) -> None:
        repo, db = _make_repo()
        tid = uuid4()
        oid = uuid4()
        node_id = uuid4()
        node = OntologyNode(
            id=node_id, ontology_id=oid, tenant_id=tid,
            node_type=NodeType.OBJECT, name="Product",
            properties={"table": "erp.products"},
        )
        db.tenant_scoped_fetch.return_value = [
            {
                "id": node_id, "ontology_id": oid, "tenant_id": tid,
                "node_type": "object", "name": "Product",
                "parent_id": None, "properties": {"table": "erp.products"},
            }
        ]
        result = await repo.create_node(tid, node)
        assert result.name == "Product"
        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "INSERT INTO knowledge.ontology_nodes" in sql
        assert "RETURNING" in sql


class TestGetSchemaMapping:
    async def test_builds_table_column_mapping(self) -> None:
        repo, db = _make_repo()
        tid = uuid4()
        cust_id = uuid4()
        name_attr_id = uuid4()
        db.tenant_scoped_fetch.side_effect = [
            # Object nodes call.
            [{"id": cust_id, "name": "Customer", "properties": {"table": "erp.customers"}}],
            # Attribute nodes call.
            [{
                "id": name_attr_id, "parent_id": cust_id, "name": "Customer.name",
                "properties": {"column": "name", "chinese_name": "客户名称", "type": "varchar"},
            }],
        ]
        mapping = await repo.get_schema_mapping(tid, uuid4())
        assert "erp.customers" in mapping
        assert mapping["erp.customers"]["name"]["chinese_name"] == "客户名称"
        assert mapping["erp.customers"]["name"]["type"] == "varchar"

    async def test_empty_mapping_when_no_nodes(self) -> None:
        repo, db = _make_repo()
        db.tenant_scoped_fetch.side_effect = [[], []]
        mapping = await repo.get_schema_mapping(uuid4(), uuid4())
        assert mapping == {}


class TestSearchNodes:
    async def test_searches_with_ilike_pattern(self) -> None:
        repo, db = _make_repo()
        db.tenant_scoped_fetch.return_value = []
        await repo.search_nodes(uuid4(), "客户", top_k=5)
        call = db.tenant_scoped_fetch.call_args
        sql = call.args[0]
        assert "name ILIKE :p0" in sql
        assert "properties::text ILIKE :p1" in sql
        assert "LIMIT :p2" in sql
        params = call.args[2:]
        assert params[0] == "%客户%"
        assert params[1] == "%客户%"
        assert params[2] == 5

    async def test_returns_parsed_nodes(self) -> None:
        repo, db = _make_repo()
        tid = uuid4()
        oid = uuid4()
        node_id = uuid4()
        db.tenant_scoped_fetch.return_value = [
            {
                "id": node_id, "ontology_id": oid, "tenant_id": tid,
                "node_type": "object", "name": "客户",
                "parent_id": None, "properties": {"table": "erp.customers"},
            }
        ]
        nodes = await repo.search_nodes(tid, "客户")
        assert len(nodes) == 1
        assert nodes[0].name == "客户"
