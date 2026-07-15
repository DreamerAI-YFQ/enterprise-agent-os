"""Tests for ConnectionManager and CredentialCrypto — external connection registry.

Unit tests mock ``DbClient`` to verify CRUD operations, credential encryption
round-trips, resolve() construction of McpClient/HttpApiConnector, and health
check propagation. Crypto tests verify Fernet encrypt/decrypt with PBKDF2.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from eaos.data.connection_manager import ConnectionManager
from eaos.data.connection_types import ConnectionSpec, ResolvedConnection
from eaos.data.crypto import CredentialCrypto

TID = UUID("00000000-0000-0000-0000-000000000001")
SECRET = "test-secret-key-for-encryption"


# ============================================================
# CredentialCrypto
# ============================================================


class TestCredentialCrypto:
    def test_encrypt_decrypt_roundtrip(self) -> None:
        crypto = CredentialCrypto(SECRET)
        original = {"api_key": "secret123", "client_id": "abc"}
        encrypted = crypto.encrypt(original)
        assert isinstance(encrypted, bytes)
        assert encrypted != json.dumps(original).encode()
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == original

    def test_different_secrets_produce_different_ciphertext(self) -> None:
        c1 = CredentialCrypto("secret-one")
        c2 = CredentialCrypto("secret-two")
        data = {"key": "value"}
        enc1 = c1.encrypt(data)
        enc2 = c2.encrypt(data)
        assert enc1 != enc2

    def test_decrypt_with_wrong_secret_fails(self) -> None:
        c1 = CredentialCrypto("secret-one")
        c2 = CredentialCrypto("secret-two")
        encrypted = c1.encrypt({"key": "value"})
        with pytest.raises(ValueError, match="decryption failed"):
            c2.decrypt(encrypted)

    def test_decrypt_tampered_token_fails(self) -> None:
        crypto = CredentialCrypto(SECRET)
        encrypted = crypto.encrypt({"key": "value"})
        tampered = encrypted[:-5] + b"XXXXX"
        with pytest.raises(ValueError, match="decryption failed"):
            crypto.decrypt(tampered)

    def test_encrypt_empty_dict(self) -> None:
        crypto = CredentialCrypto(SECRET)
        encrypted = crypto.encrypt({})
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == {}

    def test_encrypt_nested_data(self) -> None:
        crypto = CredentialCrypto(SECRET)
        original = {"oauth": {"token": "abc", "refresh": "def"}, "scopes": ["read", "write"]}
        encrypted = crypto.encrypt(original)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == original


# ============================================================
# Helpers
# ============================================================


def _mock_db(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetch_one_row: dict[str, Any] | None = None,
) -> Any:
    """Build a mock DbClient with AsyncMock methods."""
    db: Any = MagicMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.fetch_one = AsyncMock(return_value=fetch_one_row)
    db.execute = AsyncMock(return_value=None)
    return db


def _make_http_config() -> dict[str, Any]:
    """Config for an http_api connection."""
    return {
        "base_url": "https://saas.example.com",
        "resources": {
            "orders": {
                "path": "/api/v1/orders/{id}",
                "methods": ["GET", "POST"],
                "id_field": "id",
            }
        },
        "auth": {"type": "api_key", "header_name": "X-API-Key", "header_prefix": ""},
    }


def _make_mcp_config() -> dict[str, Any]:
    """Config for an mcp_stdio connection."""
    return {
        "command": "python",
        "args": ["-m", "mock_saas.mcp_server"],
        "env": {},
    }


def _row(**overrides: Any) -> dict[str, Any]:
    """Build a DB row dict with sensible defaults."""
    base: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": TID,
        "name": "test-conn",
        "type": "http_api",
        "config": _make_http_config(),
        "credentials_encrypted": None,
    }
    base.update(overrides)
    return base


# ============================================================
# ConnectionManager CRUD
# ============================================================


class TestConnectionManagerCRUD:
    async def test_register_inserts_with_encrypted_credentials(self) -> None:
        db = _mock_db()
        crypto = CredentialCrypto(SECRET)
        mgr = ConnectionManager(db, crypto)

        spec = ConnectionSpec(
            tenant_id=TID,
            name="test-conn",
            type="http_api",
            config=_make_http_config(),
            credentials={"api_key": "secret123"},
        )
        conn_id = await mgr.register(spec)
        assert isinstance(conn_id, UUID)

        db.execute.assert_called_once()
        call = db.execute.call_args
        sql = call.args[0]
        assert "INSERT INTO data.external_connections" in sql
        # Verify credentials were encrypted (not plaintext)
        encrypted_arg = call.args[6]  # sql, conn_id, tenant_id, name, type, config, creds
        assert isinstance(encrypted_arg, bytes)
        assert b"secret123" not in encrypted_arg  # not plaintext

    async def test_register_without_credentials(self) -> None:
        db = _mock_db()
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))

        spec = ConnectionSpec(
            tenant_id=TID,
            name="no-creds",
            type="http_api",
            config=_make_http_config(),
        )
        await mgr.register(spec)
        call = db.execute.call_args
        encrypted_arg = call.args[6]
        assert encrypted_arg is None

    async def test_register_invalid_type_raises(self) -> None:
        mgr = ConnectionManager(_mock_db(), CredentialCrypto(SECRET))
        spec = ConnectionSpec(
            tenant_id=TID, name="bad", type="invalid_type", config={}
        )
        with pytest.raises(ValueError, match="invalid connection type"):
            await mgr.register(spec)

    async def test_update(self) -> None:
        db = _mock_db()
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))

        conn_id = uuid4()
        spec = ConnectionSpec(
            tenant_id=TID,
            name="updated",
            type="http_api",
            config=_make_http_config(),
            credentials={"api_key": "new-key"},
        )
        await mgr.update(conn_id, spec)
        call = db.execute.call_args
        assert "UPDATE data.external_connections" in call.args[0]
        assert call.args[1] == conn_id

    async def test_delete(self) -> None:
        db = _mock_db()
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))
        conn_id = uuid4()
        await mgr.delete(conn_id)
        call = db.execute.call_args
        assert "DELETE FROM data.external_connections" in call.args[0]
        assert call.args[1] == conn_id

    async def test_list(self) -> None:
        row = _row(name="conn1", type="http_api")
        row["config"] = json.dumps(_make_http_config())
        db = _mock_db(fetch_rows=[row])
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))

        records = await mgr.list(TID)
        assert len(records) == 1
        assert records[0].name == "conn1"
        assert records[0].type == "http_api"
        assert records[0].health_status == "unknown"

    async def test_get_returns_none_if_not_found(self) -> None:
        db = _mock_db(fetch_one_row=None)
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))
        assert await mgr.get(uuid4()) is None

    async def test_get_returns_record(self) -> None:
        row = _row(name="conn1", type="mcp_stdio", config=json.dumps(_make_mcp_config()))
        db = _mock_db(fetch_one_row=row)
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))
        record = await mgr.get(row["id"])
        assert record is not None
        assert record.name == "conn1"
        assert record.type == "mcp_stdio"
        assert "command" in record.config


# ============================================================
# ConnectionManager resolve
# ============================================================


class TestConnectionManagerResolve:
    async def test_resolve_http_api(self) -> None:
        r = _row(type="http_api", credentials_encrypted=None)
        db = _mock_db(fetch_one_row=r)
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))

        resolved = await mgr.resolve(r["id"])
        assert resolved.type == "http_api"
        assert resolved.http_connector is not None
        assert resolved.mcp_client is None

    async def test_resolve_mcp_stdio(self) -> None:
        r = _row(type="mcp_stdio", config=_make_mcp_config())
        db = _mock_db(fetch_one_row=r)
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))

        resolved = await mgr.resolve(r["id"])
        assert resolved.type == "mcp_stdio"
        assert resolved.mcp_client is not None
        assert resolved.http_connector is None

    async def test_resolve_not_found_raises(self) -> None:
        db = _mock_db(fetch_one_row=None)
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))
        with pytest.raises(ValueError, match="connection not found"):
            await mgr.resolve(uuid4())

    async def test_resolve_caches_result(self) -> None:
        r = _row(type="http_api")
        db = _mock_db(fetch_one_row=r)
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))

        resolved1 = await mgr.resolve(r["id"])
        resolved2 = await mgr.resolve(r["id"])
        assert resolved1 is resolved2  # same cached instance
        # DB should only be queried once (second call hits cache)
        assert db.fetch_one.call_count == 1

    async def test_resolve_lru_eviction(self) -> None:
        """When cache is full, oldest entry is evicted."""
        mgr = ConnectionManager(_mock_db(fetch_one_row=None), CredentialCrypto(SECRET), lru_size=2)

        for i in range(3):
            r = _row(name=f"conn-{i}", type="http_api")
            mgr._db.fetch_one = AsyncMock(return_value=r)  # type: ignore[method-assign]
            await mgr.resolve(r["id"])

        assert len(mgr._cache) == 2  # evicted oldest


# ============================================================
# ConnectionManager health_check
# ============================================================


def _cached_mgr_with_mock_connector(
    *,
    healthy: bool = True,
    row: dict[str, Any] | None = None,
) -> tuple[ConnectionManager, UUID]:
    """Build a ConnectionManager with a pre-cached mock connector.

    By placing a ResolvedConnection in the LRU cache, ``resolve()`` returns
    it without hitting the DB, and ``health_check`` calls the mock.
    """
    r = row or _row(type="http_api")
    conn_id: UUID = r["id"]
    mgr = ConnectionManager(_mock_db(), CredentialCrypto(SECRET))

    mock_connector = MagicMock()
    mock_connector.health_check = AsyncMock(return_value=healthy)
    resolved = ResolvedConnection(
        conn_id=conn_id, name="test", type="http_api", http_connector=mock_connector
    )
    mgr._cache[conn_id] = resolved
    return mgr, conn_id


class TestConnectionManagerHealthCheck:
    async def test_health_check_healthy(self) -> None:
        mgr, conn_id = _cached_mgr_with_mock_connector(healthy=True)
        result = await mgr.health_check(conn_id)
        assert result.status == "healthy"
        assert result.error is None

    async def test_health_check_unhealthy(self) -> None:
        mgr, conn_id = _cached_mgr_with_mock_connector(healthy=False)
        result = await mgr.health_check(conn_id)
        assert result.status == "unhealthy"

    async def test_health_check_exception_returns_unhealthy(self) -> None:
        """When resolve raises, health_check returns unhealthy with error."""
        mgr = ConnectionManager(_mock_db(fetch_one_row=None), CredentialCrypto(SECRET))
        result = await mgr.health_check(uuid4())
        assert result.status == "unhealthy"
        assert result.error is not None

    async def test_health_check_all(self) -> None:
        r = _row(type="http_api")
        conn_id: UUID = r["id"]
        list_row: dict[str, Any] = {
            "id": conn_id,
            "tenant_id": TID,
            "name": "test",
            "type": "http_api",
            "config": json.dumps(_make_http_config()),
            "health_status": "unknown",
            "last_health_check": None,
            "created_at": None,
        }
        db = _mock_db(fetch_rows=[list_row])
        mgr = ConnectionManager(db, CredentialCrypto(SECRET))

        # Pre-cache a mock connector so health_check doesn't hit DB
        mock_connector = MagicMock()
        mock_connector.health_check = AsyncMock(return_value=True)
        mgr._cache[conn_id] = ResolvedConnection(
            conn_id=conn_id, name="test", type="http_api", http_connector=mock_connector
        )

        results = await mgr.health_check_all(TID)
        assert conn_id in results
        assert results[conn_id].status == "healthy"
