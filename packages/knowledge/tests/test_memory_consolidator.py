"""Unit tests for SessionMemoryConsolidator — mock LLMRouter + MemoryStore + DbClient."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from eaos.infra.llm.base import LLMResponse, Message
from eaos.knowledge.memory.consolidator import SessionMemoryConsolidator
from eaos.knowledge.memory.store import MemoryScope, MemoryType

TID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000201")


def _make_consolidator(
    llm_content: str = "[]",
    session_title: str | None = "ERP 数据查询",
) -> tuple[SessionMemoryConsolidator, Any, Any, Any]:
    store: Any = MagicMock()
    store.store = AsyncMock(side_effect=lambda m: m.id)
    llm: Any = MagicMock()
    llm.chat = AsyncMock(return_value=LLMResponse(content=llm_content))
    db: Any = MagicMock()
    db.tenant_scoped_fetch = AsyncMock(
        return_value=[{"title": session_title}] if session_title else []
    )
    return SessionMemoryConsolidator(store, llm, db), store, llm, db


class TestConsolidateSession:
    async def test_returns_empty_when_no_session(self) -> None:
        c, store, llm, _ = _make_consolidator(session_title=None)
        result = await c.consolidate_session(uuid4(), TID, USER_ID)
        assert result == []
        llm.chat.assert_not_awaited()
        store.store.assert_not_awaited()

    async def test_calls_llm_with_session_content(self) -> None:
        c, _, llm, _ = _make_consolidator()
        await c.consolidate_session(uuid4(), TID, USER_ID)
        llm.chat.assert_awaited_once()
        messages: list[Message] = llm.chat.call_args.args[0]
        assert messages[0].role == "system"
        assert "ERP 数据查询" in messages[1].content

    async def test_stores_extracted_insights(self) -> None:
        content = json.dumps([
            {"type": "preference", "content": "likes concise answers", "confidence": 0.9},
            {"type": "fact", "content": "uses PostgreSQL", "confidence": 0.8},
        ])
        c, store, _, _ = _make_consolidator(content)
        result = await c.consolidate_session(uuid4(), TID, USER_ID)
        assert len(result) == 2
        assert store.store.await_count == 2

    async def test_memory_scope_personal(self) -> None:
        content = json.dumps([{"type": "fact", "content": "x", "confidence": 0.5}])
        c, store, _, _ = _make_consolidator(content)
        await c.consolidate_session(uuid4(), TID, USER_ID)
        stored: Any = store.store.call_args.args[0]
        assert stored.scope == MemoryScope.PERSONAL
        assert stored.owner_id == USER_ID

    async def test_maps_memory_types(self) -> None:
        content = json.dumps([
            {"type": "preference", "content": "a"},
            {"type": "procedure", "content": "b"},
            {"type": "feedback", "content": "c"},
            {"type": "unknown", "content": "d"},
        ])
        c, store, _, _ = _make_consolidator(content)
        await c.consolidate_session(uuid4(), TID, USER_ID)
        calls = store.store.call_args_list
        assert calls[0].args[0].memory_type == MemoryType.PREFERENCE
        assert calls[1].args[0].memory_type == MemoryType.PROCEDURE
        assert calls[2].args[0].memory_type == MemoryType.FEEDBACK
        assert calls[3].args[0].memory_type == MemoryType.FACT

    async def test_returns_memory_ids(self) -> None:
        ids = [uuid4(), uuid4()]
        content = json.dumps([
            {"type": "fact", "content": "a", "confidence": 0.5},
            {"type": "fact", "content": "b", "confidence": 0.5},
        ])
        c, store, _, _ = _make_consolidator(content)
        store.store = AsyncMock(side_effect=lambda m: ids.pop(0))
        result = await c.consolidate_session(uuid4(), TID, USER_ID)
        assert len(result) == 2

    async def test_invalid_json_returns_empty(self) -> None:
        c, store, _, _ = _make_consolidator("not json at all")
        result = await c.consolidate_session(uuid4(), TID, USER_ID)
        assert result == []
        store.store.assert_not_awaited()

    async def test_empty_insights_array_returns_empty(self) -> None:
        c, store, _, _ = _make_consolidator("[]")
        result = await c.consolidate_session(uuid4(), TID, USER_ID)
        assert result == []
        store.store.assert_not_awaited()

    async def test_default_confidence_when_missing(self) -> None:
        content = json.dumps([{"type": "fact", "content": "x"}])
        c, store, _, _ = _make_consolidator(content)
        await c.consolidate_session(uuid4(), TID, USER_ID)
        stored: Any = store.store.call_args.args[0]
        assert stored.confidence == 0.5

    async def test_uses_zero_temperature(self) -> None:
        c, _, llm, _ = _make_consolidator()
        await c.consolidate_session(uuid4(), TID, USER_ID)
        assert llm.chat.call_args.kwargs["temperature"] == 0.0
