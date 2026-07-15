"""Unit tests for RedisClientImpl.

Redis is mocked to avoid a live service. Integration tests cover real behavior.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from eaos.core.config import RedisConfig
from eaos.core.errors import DataError
from eaos.infra.db.redis import RedisClientImpl
from redis import RedisError


def _make_client() -> tuple[RedisClientImpl, Any]:
    """Build a RedisClientImpl with a mocked redis.asyncio.Redis."""
    config = RedisConfig(url="redis://localhost:6379/0")
    client = RedisClientImpl(config)
    mock_redis: Any = MagicMock()
    # Most ops are async; default to AsyncMock for any attribute access.
    mock_redis.get = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()
    mock_redis.incrby = AsyncMock()
    mock_redis.exists = AsyncMock()
    mock_redis.expire = AsyncMock()
    mock_redis.eval = AsyncMock()
    client._redis = mock_redis
    return client, mock_redis


class TestBasicOps:
    async def test_get_returns_string(self) -> None:
        client, mock = _make_client()
        mock.get.return_value = b"hello"
        assert await client.get("k") == "hello"

    async def test_get_returns_none_when_missing(self) -> None:
        client, mock = _make_client()
        mock.get.return_value = None
        assert await client.get("k") is None

    async def test_get_passes_through_str(self) -> None:
        client, mock = _make_client()
        mock.get.return_value = "already-str"
        assert await client.get("k") == "already-str"

    async def test_set_passes_ttl(self) -> None:
        client, mock = _make_client()
        await client.set("k", "v", ttl=30)
        mock.set.assert_awaited_once_with("k", "v", ex=30)

    async def test_set_without_ttl(self) -> None:
        client, mock = _make_client()
        await client.set("k", "v")
        mock.set.assert_awaited_once_with("k", "v", ex=None)

    async def test_delete(self) -> None:
        client, mock = _make_client()
        await client.delete("k")
        mock.delete.assert_awaited_once_with("k")

    async def test_incrby_returns_int(self) -> None:
        client, mock = _make_client()
        mock.incrby.return_value = 5
        assert await client.incrby("counter", 2) == 5
        mock.incrby.assert_awaited_once_with("counter", 2)

    async def test_exists_returns_bool(self) -> None:
        client, mock = _make_client()
        mock.exists.return_value = 1
        assert await client.exists("k") is True
        mock.exists.return_value = 0
        assert await client.exists("k") is False

    async def test_expire(self) -> None:
        client, mock = _make_client()
        await client.expire("k", 60)
        mock.expire.assert_awaited_once_with("k", 60)


class TestErrorTranslation:
    async def test_get_translates_redis_error(self) -> None:
        client, mock = _make_client()
        mock.get.side_effect = RedisError("down")
        with pytest.raises(DataError):
            await client.get("k")

    async def test_incrby_translates_redis_error(self) -> None:
        client, mock = _make_client()
        mock.incrby.side_effect = RedisError("down")
        with pytest.raises(DataError):
            await client.incrby("c")


class TestAcquireLock:
    async def test_acquire_succeeds_first_try(self) -> None:
        client, mock = _make_client()
        mock.set.return_value = True
        acquired = await client.acquire_lock("lock:agent:1", ttl=30, retry=3)
        assert acquired is True
        # token tracked
        assert "lock:agent:1" in client._lock_tokens
        # SET called with nx=True, ex=ttl
        kwargs = mock.set.call_args.kwargs
        assert kwargs["nx"] is True
        assert kwargs["ex"] == 30

    async def test_acquire_fails_after_retries(self) -> None:
        client, mock = _make_client()
        mock.set.return_value = None  # contended every time
        acquired = await client.acquire_lock("lock:agent:1", ttl=30, retry=2)
        assert acquired is False
        assert "lock:agent:1" not in client._lock_tokens
        # Should have retried 2 times
        assert mock.set.await_count == 2

    async def test_acquire_retry_eventually_succeeds(self) -> None:
        client, mock = _make_client()
        # Fail once, then succeed
        mock.set.side_effect = [None, True]
        acquired = await client.acquire_lock("lock:agent:1", ttl=30, retry=3)
        assert acquired is True


class TestReleaseLock:
    async def test_release_calls_eval_with_stored_token(self) -> None:
        client, mock = _make_client()
        mock.set.return_value = True
        await client.acquire_lock("lock:agent:1", ttl=30)
        token = client._lock_tokens["lock:agent:1"]

        await client.release_lock("lock:agent:1")
        # eval(script, 1, key, token)
        mock.eval.assert_awaited_once()
        args = mock.eval.call_args.args
        assert args[1] == 1  # numkeys
        assert args[2] == "lock:agent:1"
        assert args[3] == token
        # token popped after release
        assert "lock:agent:1" not in client._lock_tokens

    async def test_release_no_op_if_not_owner(self) -> None:
        client, mock = _make_client()
        # Never acquired -> release is a no-op, eval not called
        await client.release_lock("lock:agent:1")
        mock.eval.assert_not_awaited()
