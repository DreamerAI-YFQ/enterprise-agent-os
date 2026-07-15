"""Agent memory engine — three-tier memory with consolidation and promotion.

Wraps MemoryStore with agent-specific logic: short-term (Redis), long-term
(personal memories), organizational (department/enterprise). Consolidation
runs async after sessions; promotion requires Harness approval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from eaos.knowledge.memory.store import Memory, MemoryScope, MemoryType

if TYPE_CHECKING:
    from eaos.knowledge.memory.consolidator import MemoryConsolidator
    from eaos.knowledge.memory.store import MemoryStore


class MemoryEngine(Protocol):
    """Agent-facing memory engine.

    Bridges short-term (session, Redis) and long-term (organizational, PG)
    memory. The agent runner calls recall() during understand node and
    consolidate_session() after session ends.
    """

    async def recall(
        self,
        query: str,
        tenant_id: UUID,
        scope: MemoryScope,
        owner_id: UUID | None,
        top_k: int = 5,
    ) -> list[Memory]:
        """Recall relevant long-term memories."""
        ...

    async def store(
        self,
        content: str,
        tenant_id: UUID,
        scope: MemoryScope,
        owner_id: UUID | None,
        memory_type: str,
        source: str = "agent",
    ) -> UUID:
        """Store a new memory."""
        ...

    async def update(
        self,
        memory_id: UUID,
        tenant_id: UUID,
        content: str | None = None,
        memory_type: str | None = None,
        confidence: float | None = None,
    ) -> None:
        """Update memory fields. Recomputes embedding when content changes."""
        ...

    async def consolidate_session(
        self,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
    ) -> list[UUID]:
        """Extract insights from session, store as personal memories.

        Called async after session ends; does not block agent response.
        """
        ...

    async def promote_to_department(
        self,
        memory_id: UUID,
        tenant_id: UUID,
        dept_id: UUID,
        approver: UUID,
    ) -> UUID:
        """Promote personal memory to department best practice.

        Requires Harness manager permission.
        """
        ...

    async def promote_to_enterprise(
        self,
        memory_id: UUID,
        tenant_id: UUID,
        approver: UUID,
    ) -> UUID:
        """Promote department memory to enterprise knowledge asset.

        Requires Harness admin permission.
        """
        ...


class MemoryEngineImpl:
    """MemoryEngine facade wrapping a MemoryStore + MemoryConsolidator.

    Delegates persistence and consolidation to Phase 2 components; adds
    agent-facing conveniences (Memory construction, scope promotion).
    """

    def __init__(self, store: MemoryStore, consolidator: MemoryConsolidator) -> None:
        self._store = store
        self._consolidator = consolidator

    async def recall(
        self,
        query: str,
        tenant_id: UUID,
        scope: MemoryScope,
        owner_id: UUID | None,
        top_k: int = 5,
    ) -> list[Memory]:
        return await self._store.recall(query, tenant_id, scope, owner_id, top_k)

    async def store(
        self,
        content: str,
        tenant_id: UUID,
        scope: MemoryScope,
        owner_id: UUID | None,
        memory_type: str,
        source: str = "agent",
    ) -> UUID:
        try:
            mem_type = MemoryType(memory_type)
        except ValueError:
            mem_type = MemoryType.FACT
        memory = Memory(
            id=uuid4(),
            tenant_id=tenant_id,
            scope=scope,
            owner_id=owner_id,
            memory_type=mem_type,
            content=content,
            source=source,
        )
        return await self._store.store(memory)

    async def update(
        self,
        memory_id: UUID,
        tenant_id: UUID,
        content: str | None = None,
        memory_type: str | None = None,
        confidence: float | None = None,
    ) -> None:
        mem_type = MemoryType(memory_type) if memory_type else None
        await self._store.update(memory_id, tenant_id, content, mem_type, confidence)

    async def consolidate_session(
        self,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
    ) -> list[UUID]:
        return await self._consolidator.consolidate_session(
            session_id, tenant_id, user_id
        )

    async def promote_to_department(
        self,
        memory_id: UUID,
        tenant_id: UUID,
        dept_id: UUID,
        approver: UUID,
    ) -> UUID:
        # TODO(Phase 4): integrate with Harness permission check
        return await self._store.promote_scope(
            memory_id, MemoryScope.DEPARTMENT, dept_id, approver
        )

    async def promote_to_enterprise(
        self,
        memory_id: UUID,
        tenant_id: UUID,
        approver: UUID,
    ) -> UUID:
        # TODO(Phase 4): integrate with Harness permission check
        del tenant_id  # promote_scope does not filter by tenant
        return await self._store.promote_scope(
            memory_id, MemoryScope.ENTERPRISE, None, approver
        )
