"""Unit tests for PgMemoryStore — mock VectorStore + Embedder + DbClient."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from eaos.core.errors import NotFoundError
from eaos.infra.vector.base import VectorSearchResult
from eaos.knowledge.memory.store import (
    Memory,
    MemoryScope,
    MemoryType,
    PgMemoryStore,
)

TID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000201")


def _make_store() -> tuple[PgMemoryStore, Any, Any, Any]:
    vs: Any = MagicMock()
    vs.search = AsyncMock()
    embedder: Any = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 8)
    db: Any = MagicMock()
    db.tenant_scoped_fetch = AsyncMock()
    db.execute = AsyncMock()
    db.fetch = AsyncMock()
    return PgMemoryStore(vs, embedder, db), vs, embedder, db


def _make_memory(content: str = "prefers concise answers") -> Memory:
    return Memory(
        id=uuid4(),
        tenant_id=TID,
        scope=MemoryScope.PERSONAL,
        owner_id=USER_ID,
        memory_type=MemoryType.PREFERENCE,
        content=content,
        confidence=0.8,
    )


def _memory_row(mid: UUID | None = None) -> dict[str, Any]:
    return {
        "id": mid or uuid4(),
        "tenant_id": TID,
        "scope": "personal",
        "owner_id": USER_ID,
        "memory_type": "preference",
        "content": "content",
        "confidence": 0.8,
        "source": "agent",
        "created_at": datetime.utcnow(),
        "last_accessed": None,
        "access_count": 0,
    }


class TestStore:
    async def test_embeds_and_inserts(self) -> None:
        store, vs, embedder, db = _make_store()
        mem = _make_memory()
        db.tenant_scoped_fetch.return_value = [{"id": mem.id}]
        result = await store.store(mem)
        embedder.embed.assert_awaited_once_with(mem.content)
        db.tenant_scoped_fetch.assert_awaited_once()
        assert result == mem.id

    async def test_insert_sql_contains_vector_cast(self) -> None:
        store, _, _, db = _make_store()
        mem = _make_memory()
        db.tenant_scoped_fetch.return_value = [{"id": mem.id}]
        await store.store(mem)
        sql = db.tenant_scoped_fetch.call_args.args[0]
        assert "CAST" in sql and "vector" in sql
        assert "knowledge.org_memories" in sql


class TestRecall:
    async def test_embeds_and_searches(self) -> None:
        store, vs, embedder, db = _make_store()
        mid = uuid4()
        vs.search.return_value = [VectorSearchResult(id=mid, content="c", score=0.1, metadata={})]
        db_row = _memory_row(mid)
        db.tenant_scoped_fetch.return_value = [db_row]
        result = await store.recall("query", TID, MemoryScope.PERSONAL, USER_ID)
        embedder.embed.assert_awaited_once_with("query")
        vs.search.assert_awaited_once()
        assert len(result) == 1
        assert result[0].id == mid

    async def test_filter_includes_scope_and_owner(self) -> None:
        store, vs, _, db = _make_store()
        vs.search.return_value = []
        db.tenant_scoped_fetch.return_value = []
        await store.recall("q", TID, MemoryScope.PERSONAL, USER_ID)
        call = vs.search.call_args
        assert call.kwargs["filter"] == {"scope": "personal", "owner_id": USER_ID}

    async def test_enterprise_scope_omits_owner(self) -> None:
        store, vs, _, db = _make_store()
        vs.search.return_value = []
        db.tenant_scoped_fetch.return_value = []
        await store.recall("q", TID, MemoryScope.ENTERPRISE, None)
        call = vs.search.call_args
        assert call.kwargs["filter"] == {"scope": "enterprise"}

    async def test_empty_results_returns_empty(self) -> None:
        store, vs, _, _ = _make_store()
        vs.search.return_value = []
        result = await store.recall("q", TID, MemoryScope.PERSONAL, USER_ID)
        assert result == []

    async def test_returns_memory_objects(self) -> None:
        store, vs, _, db = _make_store()
        mid = uuid4()
        vs.search.return_value = [VectorSearchResult(id=mid, content="c", score=0.1, metadata={})]
        db.tenant_scoped_fetch.return_value = [_memory_row(mid)]
        result = await store.recall("q", TID, MemoryScope.PERSONAL, USER_ID)
        assert isinstance(result[0], Memory)
        assert result[0].scope == MemoryScope.PERSONAL


class TestGet:
    async def test_returns_memory(self) -> None:
        store, _, _, db = _make_store()
        mid = uuid4()
        db.tenant_scoped_fetch.return_value = [_memory_row(mid)]
        result = await store.get(mid, TID)
        assert result.id == mid

    async def test_raises_not_found(self) -> None:
        store, _, _, db = _make_store()
        db.tenant_scoped_fetch.return_value = []
        with pytest.raises(NotFoundError):
            await store.get(uuid4(), TID)


class TestPromoteScope:
    async def test_updates_scope_and_owner(self) -> None:
        store, _, _, db = _make_store()
        mid = uuid4()
        db.fetch.return_value = [{"id": mid}]
        result = await store.promote_scope(mid, MemoryScope.DEPARTMENT, uuid4(), uuid4())
        assert result == mid
        sql = db.fetch.call_args.args[0]
        assert "UPDATE" in sql and "scope" in sql

    async def test_raises_not_found(self) -> None:
        store, _, _, db = _make_store()
        db.fetch.return_value = []
        with pytest.raises(NotFoundError):
            await store.promote_scope(uuid4(), MemoryScope.ENTERPRISE, None, uuid4())


class TestDelete:
    async def test_deletes_by_id_and_tenant(self) -> None:
        store, _, _, db = _make_store()
        mid = uuid4()
        await store.delete(mid, TID)
        db.execute.assert_awaited_once()
        sql = db.execute.call_args.args[0]
        assert "DELETE FROM knowledge.org_memories" in sql
        assert db.execute.call_args.args[1] == mid
        assert db.execute.call_args.args[2] == TID


class TestTouch:
    async def test_updates_access_fields(self) -> None:
        store, _, _, db = _make_store()
        mid = uuid4()
        await store.touch(mid, TID)
        db.execute.assert_awaited_once()
        sql = db.execute.call_args.args[0]
        assert "last_accessed" in sql and "access_count" in sql
        assert "now()" in sql
