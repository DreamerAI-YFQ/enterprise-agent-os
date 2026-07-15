"""Memory consolidator — extract memories from sessions.

Called asynchronously after a session ends: extracts insights from the
conversation and stores them as personal-scope memories.

Phase 2 simplification: session content is derived from the ``agent.sessions``
title only (no ``trace.spans`` query). Phase 3 will wire real conversation data.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from eaos.infra.llm.base import LLMResponse, Message
from eaos.knowledge.memory.store import Memory, MemoryScope, MemoryType

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient
    from eaos.infra.llm.router import LLMRouter
    from eaos.knowledge.memory.store import MemoryStore


_SYSTEM_PROMPT = (
    "你是对话洞察提取专家。从会话中提取值得长期记住的洞察。"
    "只返回 JSON 数组: [{\"type\": \"preference|fact|procedure|feedback\", "
    "\"content\": \"...\", \"confidence\": 0.0-1.0}]"
)


class MemoryConsolidator(Protocol):
    """Consolidates a session's interactions into durable memories."""

    async def consolidate_session(
        self,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
    ) -> list[UUID]:
        """Extract insights from session, store as personal memories."""
        ...


class SessionMemoryConsolidator:
    """MemoryConsolidator backed by LLM extraction + MemoryStore persistence."""

    def __init__(
        self,
        memory_store: MemoryStore,
        llm: LLMRouter,
        db: DbClient,
    ) -> None:
        self._store = memory_store
        self._llm = llm
        self._db = db

    async def consolidate_session(
        self,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
    ) -> list[UUID]:
        session_content = await self._fetch_session_content(session_id, tenant_id)
        if not session_content:
            return []

        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=f"会话: {session_content}"),
        ]
        response = await self._llm.chat(messages, temperature=0.0, task_type="consolidate")
        insights = self._parse_insights(response)

        memory_ids: list[UUID] = []
        for insight in insights:
            mem_type = self._map_type(insight.get("type", "fact"))
            memory = Memory(
                id=uuid4(),
                tenant_id=tenant_id,
                scope=MemoryScope.PERSONAL,
                owner_id=user_id,
                memory_type=mem_type,
                content=str(insight.get("content", "")),
                confidence=float(insight.get("confidence", 0.5)),
                source="agent",
            )
            mid = await self._store.store(memory)
            memory_ids.append(mid)
        return memory_ids

    async def _fetch_session_content(self, session_id: UUID, tenant_id: UUID) -> str:
        rows = await self._db.tenant_scoped_fetch(
            "SELECT title FROM agent.sessions WHERE id = :p0",
            tenant_id,
            session_id,
        )
        if not rows:
            return ""
        title = rows[0].get("title") or ""
        return f"会话主题: {title}" if title else ""

    @staticmethod
    def _parse_insights(response: LLMResponse) -> list[dict[str, Any]]:
        content = response.content.strip()
        start = content.find("[")
        end = content.rfind("]")
        if start == -1 or end == -1:
            return []
        try:
            data: Any = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _map_type(type_str: str) -> MemoryType:
        mapping = {
            "preference": MemoryType.PREFERENCE,
            "fact": MemoryType.FACT,
            "procedure": MemoryType.PROCEDURE,
            "feedback": MemoryType.FEEDBACK,
        }
        return mapping.get(type_str, MemoryType.FACT)
