"""Unit tests for PgVectorStore.

DbClient is mocked to avoid a live PostgreSQL. Tests verify SQL construction,
table whitelist enforcement, filter/update key validation, and result parsing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from eaos.core.errors import DataError
from eaos.infra.db.base import DbClient
from eaos.infra.vector.base import VectorSearchResult
from eaos.infra.vector.pgvector_store import PgVectorStore


def _make_store() -> tuple[PgVectorStore, Any]:
    """Build a PgVectorStore with a mocked DbClient.

    Returns (store, mock_db) so tests can inspect calls.
    """
    db: Any = MagicMock(spec=DbClient)
    db.tenant_scoped_fetch = AsyncMock()
    db.execute = AsyncMock()
    db.execute_many = AsyncMock()
    store = PgVectorStore(db)
    return store, db


class TestTableWhitelist:
    async def test_search_rejects_unknown_table(self) -> None:
        store, _ = _make_store()
        with pytest.raises(DataError, match="table not allowed"):
            await store.search([0.1], uuid4(), "public.users")

    async def test_search_rejects_injection_attempt(self) -> None:
        store, _ = _make_store()
        with pytest.raises(DataError):
            await store.search(
                [0.1], uuid4(), "knowledge.chunks; DROP TABLE knowledge.chunks"
            )

    async def test_insert_rejects_unknown_table(self) -> None:
        store, _ = _make_store()
        with pytest.raises(DataError):
            await store.insert("evil; DROP", [{"content": "x", "embedding": [0.1]}], uuid4())

    async def test_delete_rejects_unknown_table(self) -> None:
        store, _ = _make_store()
        with pytest.raises(DataError):
            await store.delete("public.users", [uuid4()], uuid4())

    async def test_update_rejects_unknown_table(self) -> None:
        store, _ = _make_store()
        with pytest.raises(DataError):
            await store.update("evil", uuid4(), {"content": "x"}, uuid4())


class TestSearch:
    async def test_search_uses_cosine_distance(self) -> None:
        store, db = _make_store()
        db.tenant_scoped_fetch.return_value = []
        await store.search([0.1, 0.2], uuid4(), "knowledge.chunks", top_k=5)
        call = db.tenant_scoped_fetch.call_args
        sql = call.args[0]
        assert "<=>" in sql
        assert "CAST(:p0 AS vector)" in sql
        assert "tenant_id = :tenant_id" in sql
        assert "ORDER BY score" in sql

    async def test_search_passes_embedding_and_topk(self) -> None:
        store, db = _make_store()
        db.tenant_scoped_fetch.return_value = []
        emb = [0.1, 0.2, 0.3]
        await store.search(emb, uuid4(), "knowledge.chunks", top_k=10)
        call = db.tenant_scoped_fetch.call_args
        params = call.args[2:]  # args[0]=sql, args[1]=tenant_id, args[2:]=*params
        assert params[0] == str(emb)  # :p0 = embedding as pgvector text
        assert params[1] == 10  # :p1 = top_k

    async def test_search_returns_parsed_results(self) -> None:
        store, db = _make_store()
        tid = uuid4()
        row_id = uuid4()
        db.tenant_scoped_fetch.return_value = [
            {
                "id": row_id,
                "score": 0.123,
            }
        ]
        results = await store.search([0.1], tid, "knowledge.chunks")
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, VectorSearchResult)
        assert r.id == row_id
        assert r.score == 0.123

    async def test_search_handles_null_metadata(self) -> None:
        store, db = _make_store()
        db.tenant_scoped_fetch.return_value = [
            {"id": uuid4(), "score": 0.5}
        ]
        results = await store.search([0.1], uuid4(), "knowledge.chunks")
        assert results[0].metadata == {}

    async def test_search_applies_filter(self) -> None:
        store, db = _make_store()
        db.tenant_scoped_fetch.return_value = []
        await store.search(
            [0.1], uuid4(), "knowledge.chunks", filter={"source": "doc1"}
        )
        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "AND source = :p1" in sql

    async def test_search_rejects_invalid_filter_key(self) -> None:
        store, _ = _make_store()
        with pytest.raises(DataError, match="invalid filter key"):
            await store.search(
                [0.1], uuid4(), "knowledge.chunks", filter={"bad key!": "v"}
            )


class TestInsert:
    async def test_insert_empty_is_noop(self) -> None:
        store, db = _make_store()
        await store.insert("knowledge.chunks", [], uuid4())
        db.execute_many.assert_not_called()

    async def test_insert_adds_tenant_id(self) -> None:
        store, db = _make_store()
        tid = uuid4()
        items = [{"content": "a", "embedding": [0.1]}, {"content": "b", "embedding": [0.2]}]
        await store.insert("knowledge.chunks", items, tid)
        call = db.execute_many.call_args
        sql = call.args[0]
        assert "INSERT INTO knowledge.chunks" in sql
        assert "CAST(" in sql and "AS vector" in sql
        params_list = call.args[1]
        assert len(params_list) == 2
        # Each row should include tenant_id and embedding as string
        for row in params_list:
            assert tid in row
            # embedding is converted to pgvector text format
            emb_values = [v for v in row if isinstance(v, str) and v.startswith("[")]
            assert len(emb_values) == 1

    async def test_insert_rejects_invalid_column(self) -> None:
        store, _ = _make_store()
        items = [{"bad col!": "v", "embedding": [0.1]}]
        with pytest.raises(DataError, match="invalid column key"):
            await store.insert("knowledge.chunks", items, uuid4())


class TestDelete:
    async def test_delete_empty_is_noop(self) -> None:
        store, db = _make_store()
        await store.delete("knowledge.chunks", [], uuid4())
        db.execute.assert_not_called()

    async def test_delete_constructs_in_clause(self) -> None:
        store, db = _make_store()
        id1, id2 = uuid4(), uuid4()
        tid = uuid4()
        await store.delete("knowledge.chunks", [id1, id2], tid)
        call = db.execute.call_args
        sql = call.args[0]
        assert "DELETE FROM knowledge.chunks" in sql
        assert "id IN (:p0, :p1)" in sql
        assert "tenant_id = :p2" in sql
        params = call.args[1:]
        assert id1 in params and id2 in params and tid in params


class TestUpdate:
    async def test_update_empty_is_noop(self) -> None:
        store, db = _make_store()
        await store.update("knowledge.chunks", uuid4(), {}, uuid4())
        db.execute.assert_not_called()

    async def test_update_constructs_set_clause(self) -> None:
        store, db = _make_store()
        item_id = uuid4()
        tid = uuid4()
        await store.update(
            "knowledge.chunks",
            item_id,
            {"content": "new text", "metadata": {"k": "v"}},
            tid,
        )
        call = db.execute.call_args
        sql = call.args[0]
        assert "UPDATE knowledge.chunks SET" in sql
        assert "content = :p0" in sql
        assert "metadata = :p1" in sql
        assert "id = :p2" in sql
        assert "tenant_id = :p3" in sql

    async def test_update_rejects_invalid_key(self) -> None:
        store, _ = _make_store()
        with pytest.raises(DataError, match="invalid update key"):
            await store.update("knowledge.chunks", uuid4(), {"bad key!": "v"}, uuid4())
