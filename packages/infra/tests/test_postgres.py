"""Unit tests for PgClient.

Live DB behavior is covered by integration-marked tests (run with
EAOS_RUN_INTEGRATION=1). These unit tests cover pure logic: parameter
binding and the session commit/rollback contract via a fake session.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from eaos.core.errors import DataError
from eaos.infra.db.postgres import PgClient
from sqlalchemy.exc import DBAPIError


class _FakeSession:
    """Minimal AsyncSession stand-in for commit/rollback contract tests."""

    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.execute = AsyncMock()


def _client_with_fake_session(fake: _FakeSession) -> PgClient:
    """Build a PgClient whose sessionmaker yields ``fake``.

    Bypasses the real engine (no DB connection) by replacing __init__.
    """

    @asynccontextmanager
    async def fake_maker() -> Any:
        yield fake

    client = PgClient.__new__(PgClient)  # bypass __init__ (no engine)
    client._sessionmaker = fake_maker  # type: ignore[assignment]
    return client


class TestBindParams:
    def test_empty_params(self) -> None:
        assert PgClient._bind_params(()) == {}

    def test_single_param(self) -> None:
        result = PgClient._bind_params(("alice",))
        assert result == {"p0": "alice"}

    def test_multiple_params_zero_indexed(self) -> None:
        result = PgClient._bind_params(("alice", 42, True))
        assert result == {"p0": "alice", "p1": 42, "p2": True}


class TestSessionContract:
    async def test_session_commits_on_clean_exit(self) -> None:
        fake = _FakeSession()
        client = _client_with_fake_session(fake)
        async with client.session():
            pass  # clean exit
        fake.commit.assert_awaited_once()
        fake.rollback.assert_not_awaited()

    async def test_session_rolls_back_on_exception(self) -> None:
        fake = _FakeSession()
        client = _client_with_fake_session(fake)
        with pytest.raises(RuntimeError, match="boom"):
            async with client.session():
                raise RuntimeError("boom")
        fake.rollback.assert_awaited_once()
        fake.commit.assert_not_awaited()


class TestFetchErrorTranslation:
    async def test_fetch_translates_dbapi_error_to_dataerror(self) -> None:
        fake = _FakeSession()
        fake.execute.side_effect = DBAPIError(
            statement=MagicMock(), params=None, orig=Exception("db down")
        )
        client = _client_with_fake_session(fake)
        with pytest.raises(DataError):
            await client.fetch("SELECT 1")
