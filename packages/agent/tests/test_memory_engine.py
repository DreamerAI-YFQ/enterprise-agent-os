"""Tests for MemoryEngineImpl — delegation + scope promotion."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from eaos.agent.memory.engine import MemoryEngineImpl
from eaos.knowledge.memory.store import Memory, MemoryScope, MemoryType


def _memory(memory_id: UUID | None = None) -> Memory:
    return Memory(
        id=memory_id or uuid4(),
        tenant_id=uuid4(),
        scope=MemoryScope.PERSONAL,
        owner_id=uuid4(),
        memory_type=MemoryType.FACT,
        content="some content",
    )


class TestRecall:
    async def test_recall_delegates_to_store(self) -> None:
        tenant_id = uuid4()
        owner_id = uuid4()
        expected = [_memory()]
        store = AsyncMock()
        store.recall.return_value = expected
        consolidator = AsyncMock()
        engine = MemoryEngineImpl(store, consolidator)

        result = await engine.recall(
            "query", tenant_id, MemoryScope.PERSONAL, owner_id, top_k=3
        )

        assert result == expected
        store.recall.assert_awaited_once_with(
            "query", tenant_id, MemoryScope.PERSONAL, owner_id, 3
        )


class TestStore:
    async def test_store_constructs_memory_and_delegates(self) -> None:
        tenant_id = uuid4()
        owner_id = uuid4()
        new_id = uuid4()
        store = AsyncMock()
        store.store.return_value = new_id
        consolidator = AsyncMock()
        engine = MemoryEngineImpl(store, consolidator)

        result = await engine.store(
            "content",
            tenant_id,
            MemoryScope.PERSONAL,
            owner_id,
            "preference",
            source="manual",
        )

        assert result == new_id
        store.store.assert_awaited_once()
        stored_memory: Memory = store.store.call_args.args[0]
        assert stored_memory.content == "content"
        assert stored_memory.tenant_id == tenant_id
        assert stored_memory.owner_id == owner_id
        assert stored_memory.memory_type == MemoryType.PREFERENCE
        assert stored_memory.source == "manual"
        assert stored_memory.scope == MemoryScope.PERSONAL

    async def test_store_unknown_type_falls_back_to_fact(self) -> None:
        store = AsyncMock()
        store.store.return_value = uuid4()
        consolidator = AsyncMock()
        engine = MemoryEngineImpl(store, consolidator)

        await engine.store(
            "c", uuid4(), MemoryScope.PERSONAL, uuid4(), "unknown_type"
        )

        stored_memory: Memory = store.store.call_args.args[0]
        assert stored_memory.memory_type == MemoryType.FACT


class TestConsolidateSession:
    async def test_consolidate_delegates(self) -> None:
        session_id = uuid4()
        tenant_id = uuid4()
        user_id = uuid4()
        ids = [uuid4(), uuid4()]
        store = AsyncMock()
        consolidator = AsyncMock()
        consolidator.consolidate_session.return_value = ids
        engine = MemoryEngineImpl(store, consolidator)

        result = await engine.consolidate_session(session_id, tenant_id, user_id)

        assert result == ids
        consolidator.consolidate_session.assert_awaited_once_with(
            session_id, tenant_id, user_id
        )


class TestPromotion:
    async def test_promote_to_department(self) -> None:
        memory_id = uuid4()
        dept_id = uuid4()
        approver = uuid4()
        new_id = uuid4()
        store = AsyncMock()
        store.promote_scope.return_value = new_id
        consolidator = AsyncMock()
        engine = MemoryEngineImpl(store, consolidator)

        result = await engine.promote_to_department(
            memory_id, uuid4(), dept_id, approver
        )

        assert result == new_id
        store.promote_scope.assert_awaited_once_with(
            memory_id, MemoryScope.DEPARTMENT, dept_id, approver
        )

    async def test_promote_to_enterprise(self) -> None:
        memory_id = uuid4()
        approver = uuid4()
        new_id = uuid4()
        store = AsyncMock()
        store.promote_scope.return_value = new_id
        consolidator = AsyncMock()
        engine = MemoryEngineImpl(store, consolidator)

        result = await engine.promote_to_enterprise(memory_id, uuid4(), approver)

        assert result == new_id
        store.promote_scope.assert_awaited_once_with(
            memory_id, MemoryScope.ENTERPRISE, None, approver
        )
