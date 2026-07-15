"""Redis adapter implementing RedisClient.

Backed by ``redis.asyncio``. Adds distributed locking via SET NX + token +
Lua compare-and-delete for department-shared-agent thread coordination.

Lock safety: each instance tracks the token it used per lock key, so
``release_lock`` only deletes its own lock (compare-and-delete via Lua).
This is safe within a single process; cross-process lock ownership requires
a token-returning acquire API (deferred — not needed for M1 single-api relay).
"""

from __future__ import annotations

import asyncio
import secrets
from typing import TYPE_CHECKING

from eaos.core.errors import DataError
from redis import RedisError
from redis.asyncio import Redis

if TYPE_CHECKING:
    from eaos.core.config import RedisConfig


# Lua: only delete if current value equals the stored token.
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


class RedisClientImpl:
    """Async Redis client. Implements the RedisClient protocol."""

    def __init__(self, config: RedisConfig) -> None:
        self._config = config
        self._redis: Redis = Redis.from_url(
            config.url, max_connections=config.max_connections
        )
        # Track tokens for locks we own, keyed by lock key.
        self._lock_tokens: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        try:
            value = await self._redis.get(key)
        except RedisError as exc:
            raise DataError(f"redis get failed: {exc}") from exc
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else value

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        try:
            await self._redis.set(key, value, ex=ttl)
        except RedisError as exc:
            raise DataError(f"redis set failed: {exc}") from exc

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except RedisError as exc:
            raise DataError(f"redis delete failed: {exc}") from exc

    async def incrby(self, key: str, amount: int = 1) -> int:
        try:
            return int(await self._redis.incrby(key, amount))
        except RedisError as exc:
            raise DataError(f"redis incrby failed: {exc}") from exc

    async def exists(self, key: str) -> bool:
        try:
            return bool(await self._redis.exists(key))
        except RedisError as exc:
            raise DataError(f"redis exists failed: {exc}") from exc

    async def expire(self, key: str, ttl: int) -> None:
        try:
            await self._redis.expire(key, ttl)
        except RedisError as exc:
            raise DataError(f"redis expire failed: {exc}") from exc

    async def acquire_lock(self, key: str, ttl: int = 30, retry: int = 3) -> bool:
        """Acquire a distributed lock with TTL via SET NX.

        Retries up to ``retry`` times with 100ms backoff. Returns True if
        acquired, False if contended after all retries.
        """
        token = secrets.token_hex(16)
        for _ in range(max(retry, 1)):
            try:
                acquired = await self._redis.set(key, token, nx=True, ex=ttl)
            except RedisError as exc:
                raise DataError(f"redis acquire_lock failed: {exc}") from exc
            if acquired:
                self._lock_tokens[key] = token
                return True
            await asyncio.sleep(0.1)
        return False

    async def release_lock(self, key: str) -> None:
        """Release a lock we own via Lua compare-and-delete.

        No-op if we did not acquire the lock or it has already expired.
        """
        token = self._lock_tokens.pop(key, None)
        if token is None:
            return
        try:
            await self._redis.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)
        except RedisError as exc:
            raise DataError(f"redis release_lock failed: {exc}") from exc

    async def close(self) -> None:
        """Close the connection pool. Call on application shutdown."""
        await self._redis.aclose()
