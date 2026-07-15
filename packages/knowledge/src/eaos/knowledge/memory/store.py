"""Organizational memory — three-tier knowledge compounding.

Personal -> Department -> Enterprise. Promotion requires approval (Harness
permission check). This is the engine of the "knowledge compounding flywheel".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient
    from eaos.infra.vector.base import Embedder, VectorStore


class MemoryScope(StrEnum):
    """Three-tier visibility scope."""

    PERSONAL = "personal"
    DEPARTMENT = "department"
    ENTERPRISE = "enterprise"


class MemoryType(StrEnum):
    """Memory content type."""

    PREFERENCE = "preference"  # user preference
    FACT = "fact"  # factual knowledge
    PROCEDURE = "procedure"  # how-to knowledge
    FEEDBACK = "feedback"  # task feedback signal


@dataclass(frozen=True)
class Memory:
    """A single organizational memory entry."""

    id: UUID
    tenant_id: UUID
    scope: MemoryScope
    owner_id: UUID | None  # personal: user_id; dept: dept_id; enterprise: None
    memory_type: MemoryType
    content: str
    confidence: float = 0.5
    source: str = "agent"  # agent/manual/rl
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_accessed: datetime | None = None
    access_count: int = 0


class MemoryStore(Protocol):
    """Organizational memory storage with three-tier scope."""

    async def store(self, memory: Memory) -> UUID:
        """Store a new memory."""
        ...

    async def recall(
        self,
        query: str,
        tenant_id: UUID,
        scope: MemoryScope,
        owner_id: UUID | None,
        top_k: int = 5,
    ) -> list[Memory]:
        """Recall relevant memories by semantic search, scoped."""
        ...

    async def get(self, memory_id: UUID, tenant_id: UUID) -> Memory:
        """Fetch by id."""
        ...

    async def promote_scope(
        self,
        memory_id: UUID,
        new_scope: MemoryScope,
        new_owner_id: UUID | None,
        approver: UUID,
    ) -> UUID:
        """Promote memory to a higher scope (personal->dept->enterprise).

        Requires Harness permission (manager for dept, admin for enterprise).
        Returns the (possibly new) memory id.
        """
        ...

    async def delete(self, memory_id: UUID, tenant_id: UUID) -> None:
        """Delete a memory."""
        ...

    async def update(
        self,
        memory_id: UUID,
        tenant_id: UUID,
        content: str | None = None,
        memory_type: MemoryType | None = None,
        confidence: float | None = None,
    ) -> None:
        """Update memory fields. Recomputes embedding when content changes."""
        ...

    async def touch(self, memory_id: UUID, tenant_id: UUID) -> None:
        """Update last_accessed and increment access_count."""
        ...


class PgMemoryStore:
    """MemoryStore backed by PostgreSQL + pgvector via DbClient."""

    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        db: DbClient,
    ) -> None:
        self._vs = vector_store
        self._embedder = embedder
        self._db = db

    @staticmethod
    def _row_to_memory(row: dict[str, Any]) -> Memory:
        return Memory(
            id=row["id"],
            tenant_id=row["tenant_id"],
            scope=MemoryScope(row["scope"]),
            owner_id=row.get("owner_id"),
            memory_type=MemoryType(row["memory_type"]),
            content=row["content"],
            confidence=float(row.get("confidence", 0.5)),
            source=row.get("source", "agent"),
            created_at=row.get("created_at", datetime.utcnow()),
            last_accessed=row.get("last_accessed"),
            access_count=int(row.get("access_count", 0)),
        )

    async def store(self, memory: Memory) -> UUID:
        embedding = await self._embedder.embed(memory.content)
        rows = await self._db.tenant_scoped_fetch(
            "INSERT INTO knowledge.org_memories "
            "(id, tenant_id, scope, owner_id, memory_type, content, "
            "embedding, confidence, source) "
            "VALUES (:p0, :tenant_id, :p1, :p2, :p3, :p4, CAST(:p5 AS vector), :p6, :p7) "
            "RETURNING id",
            memory.tenant_id,
            memory.id,
            str(memory.scope.value),
            memory.owner_id,
            str(memory.memory_type.value),
            memory.content,
            str(embedding),
            memory.confidence,
            memory.source,
        )
        return cast("UUID", rows[0]["id"])

    async def recall(
        self,
        query: str,
        tenant_id: UUID,
        scope: MemoryScope,
        owner_id: UUID | None,
        top_k: int = 5,
    ) -> list[Memory]:
        embedding = await self._embedder.embed(query)
        filter_dict: dict[str, Any] = {"scope": str(scope.value)}
        if owner_id is not None:
            filter_dict["owner_id"] = owner_id
        results = await self._vs.search(
            embedding,
            tenant_id,
            "knowledge.org_memories",
            top_k=top_k,
            filter=filter_dict,
        )
        if not results:
            return []
        ids = [r.id for r in results]
        placeholders = ", ".join(f":p{i}" for i in range(len(ids)))
        rows = await self._db.tenant_scoped_fetch(
            f"SELECT id, tenant_id, scope, owner_id, memory_type, content, "
            f"confidence, source, created_at, last_accessed, access_count "
            f"FROM knowledge.org_memories "
            f"WHERE tenant_id = :tenant_id AND id IN ({placeholders})",
            tenant_id,
            *ids,
        )
        by_id = {r["id"]: r for r in rows}
        return [self._row_to_memory(by_id[r.id]) for r in results if r.id in by_id]

    async def get(self, memory_id: UUID, tenant_id: UUID) -> Memory:
        from eaos.core.errors import NotFoundError

        rows = await self._db.tenant_scoped_fetch(
            "SELECT id, tenant_id, scope, owner_id, memory_type, content, "
            "confidence, source, created_at, last_accessed, access_count "
            "FROM knowledge.org_memories "
            "WHERE id = :p0 AND tenant_id = :tenant_id",
            tenant_id,
            memory_id,
        )
        if not rows:
            raise NotFoundError(f"memory {memory_id} not found")
        return self._row_to_memory(rows[0])

    async def promote_scope(
        self,
        memory_id: UUID,
        new_scope: MemoryScope,
        new_owner_id: UUID | None,
        approver: UUID,
    ) -> UUID:
        # TODO(Phase 4): integrate with Harness permission check
        del approver  # unused until Phase 4
        rows = await self._db.fetch(
            "UPDATE knowledge.org_memories "
            "SET scope = :p0, owner_id = :p1 "
            "WHERE id = :p2 RETURNING id",
            str(new_scope.value),
            new_owner_id,
            memory_id,
        )
        if not rows:
            from eaos.core.errors import NotFoundError

            raise NotFoundError(f"memory {memory_id} not found")
        return cast("UUID", rows[0]["id"])

    async def delete(self, memory_id: UUID, tenant_id: UUID) -> None:
        await self._db.execute(
            "DELETE FROM knowledge.org_memories WHERE id = :p0 AND tenant_id = :p1",
            memory_id,
            tenant_id,
        )

    async def update(
        self,
        memory_id: UUID,
        tenant_id: UUID,
        content: str | None = None,
        memory_type: MemoryType | None = None,
        confidence: float | None = None,
    ) -> None:
        """Update memory fields. Recomputes embedding when content changes."""
        sets: list[str] = []
        args: list[Any] = []
        if content is not None:
            embedding = await self._embedder.embed(content)
            sets.append(f"content = :p{len(args)}")
            args.append(content)
            sets.append(f"embedding = CAST(:p{len(args)} AS vector)")
            args.append(str(embedding))
        if memory_type is not None:
            sets.append(f"memory_type = :p{len(args)}")
            args.append(str(memory_type.value))
        if confidence is not None:
            sets.append(f"confidence = :p{len(args)}")
            args.append(confidence)
        if not sets:
            return
        set_clause = ", ".join(sets)
        args.append(memory_id)
        args.append(tenant_id)
        idx_id = len(args) - 2
        idx_tenant = len(args) - 1
        await self._db.execute(
            f"UPDATE knowledge.org_memories SET {set_clause} "
            f"WHERE id = :p{idx_id} AND tenant_id = :p{idx_tenant}",
            *args,
        )

    async def touch(self, memory_id: UUID, tenant_id: UUID) -> None:
        await self._db.execute(
            "UPDATE knowledge.org_memories "
            "SET last_accessed = now(), access_count = access_count + 1 "
            "WHERE id = :p0 AND tenant_id = :p1",
            memory_id,
            tenant_id,
        )
