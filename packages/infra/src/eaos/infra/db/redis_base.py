"""Redis client protocol.

Used for: short-term session memory (TTL), quota counters (atomic INCR),
hot caches, distributed locks for shared-agent thread_id coordination.
"""

from __future__ import annotations

from typing import Protocol


class RedisClient(Protocol):
    """Async Redis client."""

    async def get(self, key: str) -> str | None:
        """Get string value, or None if missing/expired."""
        ...

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        """Set string value with optional TTL in seconds."""
        ...

    async def delete(self, key: str) -> None:
        """Delete a key. No-op if missing."""
        ...

    async def incrby(self, key: str, amount: int = 1) -> int:
        """Atomically increment a counter. Returns new value.

        Used for quota tracking — atomicity is critical to avoid races when
        multiple Agent invocations consume tokens concurrently.
        """
        ...

    async def exists(self, key: str) -> bool:
        """Check key existence."""
        ...

    async def expire(self, key: str, ttl: int) -> None:
        """Set TTL on an existing key."""
        ...

    async def acquire_lock(self, key: str, ttl: int = 30, retry: int = 3) -> bool:
        """Acquire a distributed lock with TTL.

        Used to serialize concurrent invocations on the same shared thread_id
        (department agent relay). Returns True if acquired, False if contended.
        """
        ...

    async def release_lock(self, key: str) -> None:
        """Release a previously acquired lock."""
        ...
