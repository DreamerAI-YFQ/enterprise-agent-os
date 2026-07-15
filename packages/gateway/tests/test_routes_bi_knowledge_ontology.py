"""Tests for /bi, /admin/knowledge/documents, and /admin/ontology API routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.data.connector import DataResource, SchemaDescription
from eaos.data.text2sql.engine import QueryResult
from eaos.gateway.api.app import create_app
from eaos.knowledge.ontology.model import NodeType, OntologyNode
from httpx import ASGITransport, AsyncClient

SECRET = "f0-t7-t9-t10-secret-32byte!"
TID = UUID("00000000-0000-0000-0000-000000000001")
ADMIN_ID = UUID("00000000-0000-0000-0000-000000000010")
EMP_ID = UUID("00000000-0000-0000-0000-000000000020")
ONTOLOGY_ID = UUID("00000000-0000-0000-0000-000000000040")


def _config() -> AppConfig:
    return AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]


def _admin_token() -> str:
    return create_jwt_token(SECRET, ADMIN_ID, TID, "admin")


def _employee_token() -> str:
    return create_jwt_token(SECRET, EMP_ID, TID, "employee")


def _mock_db(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    single_row: dict[str, Any] | None = None,
) -> Any:
    db: Any = AsyncMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.fetch_one = AsyncMock(return_value=single_row)
    db.execute = AsyncMock(return_value=None)
    return db


# ============================================================
# BI — /bi/query
# ============================================================


class TestBiQuery:
    async def test_query_returns_results(self) -> None:
        result = QueryResult(
            rows=[{"customer": "Acme", "total": 5000}],
            sql="SELECT * FROM erp.orders",
            explanation="Listed all orders",
        )
        engine: Any = AsyncMock()
        engine.query = AsyncMock(return_value=result)

        app = create_app(_config())
        app.state.text2sql_engine = engine
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/bi/query",
                json={
                    "query": "show me all orders",
                    "datasource_id": str(uuid4()),
                },
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sql"] == "SELECT * FROM erp.orders"
        assert len(data["rows"]) == 1
        assert data["error"] is None

    async def test_query_no_token_returns_401(self) -> None:
        app = create_app(_config())
        app.state.text2sql_engine = AsyncMock()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/bi/query",
                json={"query": "test", "datasource_id": str(uuid4())},
            )
        assert resp.status_code == 401


# ============================================================
# BI — /admin/bi/sql
# ============================================================


class TestAdminSqlConsole:
    async def test_admin_executes_sql(self) -> None:
        sandbox: Any = AsyncMock()
        sandbox.execute_readonly = AsyncMock(
            return_value=[{"col": "val1"}, {"col": "val2"}]
        )
        app = create_app(_config())
        app.state.sql_sandbox = sandbox
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/bi/sql",
                json={"sql": "SELECT * FROM erp.customers"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 2
        assert len(data["rows"]) == 2

    async def test_employee_forbidden(self) -> None:
        app = create_app(_config())
        app.state.sql_sandbox = AsyncMock()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/bi/sql",
                json={"sql": "SELECT 1"},
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403


# ============================================================
# BI — /admin/bi/datasources + tables
# ============================================================


class TestBiDatasources:
    async def test_list_datasources(self) -> None:
        ds_id = uuid4()
        rows = [
            {
                "id": ds_id,
                "name": "ERP-Prod",
                "source_type": "erp",
                "access_mode": "read",
                "status": "active",
                "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            }
        ]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=rows)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/bi/datasources",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "ERP-Prod"


class TestBiTables:
    async def test_list_tables_all_connectors(self) -> None:
        erp: Any = AsyncMock()
        erp.list_resources = AsyncMock(
            return_value=[
                DataResource(
                    name="orders",
                    display_name="订单",
                    description="Sales orders",
                    access_mode="read_write",
                )
            ]
        )
        crm: Any = AsyncMock()
        crm.list_resources = AsyncMock(
            return_value=[
                DataResource(
                    name="customers",
                    display_name="客户",
                    description="CRM customers",
                    access_mode="read",
                )
            ]
        )
        app = create_app(_config())
        app.state.data_connectors = {"erp": erp, "crm": crm}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/bi/tables",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        connectors = {r["connector"] for r in data}
        assert connectors == {"erp", "crm"}

    async def test_describe_table(self) -> None:
        schema = SchemaDescription(
            table_name="orders",
            columns=[{"name": "order_no", "type": "varchar", "nullable": False}],
            relations=[],
            sample_rows=[],
        )
        erp: Any = AsyncMock()
        erp.list_resources = AsyncMock(
            return_value=[DataResource("orders", "订单", "desc", "read_write")]
        )
        erp.describe_schema = AsyncMock(return_value=schema)
        app = create_app(_config())
        app.state.data_connectors = {"erp": erp}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/bi/tables/orders?connector=erp",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["table_name"] == "orders"
        assert len(data["columns"]) == 1

    async def test_describe_table_not_found(self) -> None:
        erp: Any = AsyncMock()
        erp.list_resources = AsyncMock(return_value=[])
        app = create_app(_config())
        app.state.data_connectors = {"erp": erp}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/bi/tables/nonexistent",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 404


# ============================================================
# Knowledge documents — /admin/knowledge/documents
# ============================================================


def _doc_row(*, doc_id: UUID | None = None) -> dict[str, Any]:
    return {
        "id": doc_id or uuid4(),
        "tenant_id": TID,
        "source_type": "pdf",
        "source_uri": "s3://bucket/doc.pdf",
        "title": "ERP Manual",
        "content_hash": "abc123",
        "version": 1,
        "metadata": {},
        "status": "indexed",
        "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    }


class TestKnowledgeDocuments:
    async def test_list_documents(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=[_doc_row(), _doc_row()])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/knowledge/documents",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_get_document_not_found(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/admin/knowledge/documents/{uuid4()}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 404

    async def test_ingest_document(self) -> None:
        doc_id = uuid4()
        rag: Any = AsyncMock()
        rag.ingest = AsyncMock(return_value=[uuid4(), uuid4()])
        app = create_app(_config())
        app.state.rag_pipeline = rag
        app.state.db = _mock_db(single_row=_doc_row(doc_id=doc_id))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/knowledge/documents",
                json={
                    "source_type": "pdf",
                    "source_uri": "s3://bucket/doc.pdf",
                    "title": "ERP Manual",
                    "content": "This is the full document text...",
                },
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["chunk_count"] == 2
        assert data["title"] == "ERP Manual"

    async def test_delete_document(self) -> None:
        doc_id = uuid4()
        app = create_app(_config())
        app.state.db = _mock_db(single_row={"id": doc_id})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                f"/admin/knowledge/documents/{doc_id}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 204
        # verify both chunks and document were deleted
        assert app.state.db.execute.await_count == 2

    async def test_delete_not_found(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                f"/admin/knowledge/documents/{uuid4()}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 404

    async def test_employee_forbidden(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/knowledge/documents",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403


# ============================================================
# Ontology — /admin/ontology/terms + /gaps
# ============================================================


def _ontology_node(
    *, node_id: UUID | None = None, name: str = "Customer"
) -> OntologyNode:
    return OntologyNode(
        id=node_id or uuid4(),
        ontology_id=ONTOLOGY_ID,
        tenant_id=TID,
        node_type=NodeType.OBJECT,
        name=name,
        parent_id=None,
        properties={},
    )


class TestOntologyTerms:
    async def test_list_terms(self) -> None:
        from eaos.knowledge.ontology.model import Ontology

        nodes = [_ontology_node(name="Customer"), _ontology_node(name="Order")]
        repo: Any = AsyncMock()
        repo.get_active = AsyncMock(
            return_value=Ontology(
                id=ONTOLOGY_ID, tenant_id=TID, name="default", version="1.0.0",
                status="active",
            )
        )
        repo.get_nodes = AsyncMock(return_value=nodes)
        app = create_app(_config())
        app.state.ontology_repo = repo
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/ontology/terms",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "Customer"
        assert data[0]["node_type"] == "object"

    async def test_list_terms_with_ontology_id(self) -> None:
        repo: Any = AsyncMock()
        repo.get_nodes = AsyncMock(return_value=[])
        app = create_app(_config())
        app.state.ontology_repo = repo
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/admin/ontology/terms?ontology_id={ONTOLOGY_ID}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        repo.get_active.assert_not_awaited()

    async def test_create_term(self) -> None:
        node = _ontology_node()
        repo: Any = AsyncMock()
        repo.create_node = AsyncMock(return_value=node)
        repo.get_active = AsyncMock(return_value=None)
        app = create_app(_config())
        app.state.ontology_repo = repo
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/ontology/terms",
                json={
                    "ontology_id": str(ONTOLOGY_ID),
                    "node_type": "object",
                    "name": "Customer",
                },
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Customer"

    async def test_create_invalid_node_type(self) -> None:
        repo: Any = AsyncMock()
        app = create_app(_config())
        app.state.ontology_repo = repo
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/ontology/terms",
                json={
                    "ontology_id": str(ONTOLOGY_ID),
                    "node_type": "INVALID",
                    "name": "Bad",
                },
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 422

    async def test_update_term(self) -> None:
        term_id = uuid4()
        existing = {
            "id": term_id,
            "ontology_id": ONTOLOGY_ID,
            "tenant_id": TID,
            "node_type": "object",
            "name": "OldName",
            "parent_id": None,
            "properties": {},
        }
        updated = {**existing, "name": "NewName"}
        app = create_app(_config())
        app.state.db = _mock_db(single_row=updated)
        # fetch_one is called twice: first for existence check, then for result
        app.state.db.fetch_one = AsyncMock(side_effect=[existing, updated])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/admin/ontology/terms/{term_id}",
                json={"name": "NewName"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        assert resp.json()["name"] == "NewName"

    async def test_delete_term(self) -> None:
        term_id = uuid4()
        app = create_app(_config())
        app.state.db = _mock_db(single_row={"id": term_id})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                f"/admin/ontology/terms/{term_id}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 204
        app.state.db.execute.assert_awaited()

    async def test_delete_not_found(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                f"/admin/ontology/terms/{uuid4()}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 404


class TestOntologyGaps:
    async def test_list_gaps(self) -> None:
        rows = [
            {
                "id": uuid4(),
                "ontology_id": ONTOLOGY_ID,
                "node_type": "attribute",
                "name": "customer_tier",
                "parent_id": None,
            }
        ]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=rows)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/ontology/gaps",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "customer_tier"

    async def test_employee_forbidden(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/ontology/gaps",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403


# ============================================================
# Knowledge chunks — /admin/knowledge/documents/{id}/chunks
# ============================================================


def _chunk_row(*, doc_id: UUID, idx: int) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "document_id": doc_id,
        "tenant_id": TID,
        "chunk_index": idx,
        "content": f"chunk content {idx}",
        "token_count": 100 + idx,
        "metadata": {"source": "test"},
        "created_at": datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    }


class TestKnowledgeDocumentChunks:
    async def test_list_chunks(self) -> None:
        doc_id = uuid4()
        rows = [_chunk_row(doc_id=doc_id, idx=0), _chunk_row(doc_id=doc_id, idx=1)]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=rows)
        app.state.db.fetch_one = AsyncMock(return_value={"id": doc_id})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/admin/knowledge/documents/{doc_id}/chunks",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["chunk_index"] == 0
        assert data[0]["token_count"] == 100
        assert "embedding" not in data[0]

    async def test_list_chunks_document_not_found(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/admin/knowledge/documents/{uuid4()}/chunks",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 404

    async def test_list_chunks_employee_forbidden(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/admin/knowledge/documents/{uuid4()}/chunks",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403


# ============================================================
# Ontology graph — /admin/ontology/graph
# ============================================================


class TestOntologyGraph:
    async def test_get_graph(self) -> None:
        parent_id = uuid4()
        child_id = uuid4()
        rows = [
            {
                "id": parent_id,
                "ontology_id": ONTOLOGY_ID,
                "tenant_id": TID,
                "node_type": "object",
                "name": "Customer",
                "parent_id": None,
                "properties": {},
            },
            {
                "id": child_id,
                "ontology_id": ONTOLOGY_ID,
                "tenant_id": TID,
                "node_type": "attribute",
                "name": "customer_name",
                "parent_id": parent_id,
                "properties": {"type": "string"},
            },
        ]
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=rows)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/ontology/graph",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
        assert data["edges"][0]["source"] == str(parent_id)
        assert data["edges"][0]["target"] == str(child_id)
        assert data["nodes"][1]["properties"] == {"type": "string"}

    async def test_get_graph_with_ontology_id(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db(fetch_rows=[])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/admin/ontology/graph?ontology_id={ONTOLOGY_ID}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []
        assert data["edges"] == []

    async def test_get_graph_employee_forbidden(self) -> None:
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/ontology/graph",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403
