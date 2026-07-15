"""ConnectionManager — external connection registry with encrypted credentials.

Manages the lifecycle of external MCP servers and HTTP API connections:
registration, credential encryption, resolution to live clients, and
health checking. All credentials are encrypted at rest via ``CredentialCrypto``
and only decrypted inside ``resolve()``.

The manager caches resolved connections in an LRU (default size 32) to avoid
repeatedly spawning MCP subprocesses or creating HTTP clients.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx
from eaos.data.connection_types import (
    ConnectionRecord,
    ConnectionSpec,
    HealthStatus,
    ResolvedConnection,
)
from eaos.data.http_connector import HttpApiConnector
from eaos.data.http_spec import HttpApiSpec, HttpAuth, PaginationSpec, ResourceSpec
from eaos.data.mcp.client import McpClient, StdioTransport

if TYPE_CHECKING:
    from eaos.data.crypto import CredentialCrypto
    from eaos.infra.db.base import DbClient

logger = logging.getLogger(__name__)

_VALID_TYPES = {"mcp_stdio", "mcp_sse", "mcp_http", "http_api"}
_DEFAULT_LRU_SIZE = 32


class ConnectionManager:
    """Registry for external connections — CRUD + resolve + health check.

    Backed by ``data.external_connections`` (migration 0006). Credentials are
    encrypted with Fernet; only ``resolve()`` decrypts them internally.
    """

    def __init__(
        self,
        db: DbClient,
        crypto: CredentialCrypto,
        lru_size: int = _DEFAULT_LRU_SIZE,
    ) -> None:
        self._db = db
        self._crypto = crypto
        self._lru_size = lru_size
        self._cache: OrderedDict[UUID, ResolvedConnection] = OrderedDict()

    # -- CRUD -------------------------------------------------------------

    async def register(self, spec: ConnectionSpec) -> UUID:
        """Insert a new external connection with encrypted credentials."""
        if spec.type not in _VALID_TYPES:
            raise ValueError(f"invalid connection type: {spec.type}")

        conn_id = uuid4()
        creds_encrypted: bytes | None = None
        if spec.credentials:
            creds_encrypted = self._crypto.encrypt(spec.credentials)

        await self._db.execute(
            "INSERT INTO data.external_connections "
            "(id, tenant_id, name, type, config, credentials_encrypted, health_status) "
            "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, 'unknown')",
            conn_id,
            spec.tenant_id,
            spec.name,
            spec.type,
            json.dumps(spec.config),
            creds_encrypted,
        )
        return conn_id

    async def update(self, conn_id: UUID, spec: ConnectionSpec) -> None:
        """Update name, type, config, and credentials for a connection."""
        creds_encrypted: bytes | None = None
        if spec.credentials:
            creds_encrypted = self._crypto.encrypt(spec.credentials)

        await self._db.execute(
            "UPDATE data.external_connections "
            "SET name = :p1, type = :p2, config = :p3, credentials_encrypted = :p4, "
            "updated_at = now() WHERE id = :p0",
            conn_id,
            spec.name,
            spec.type,
            json.dumps(spec.config),
            creds_encrypted,
        )
        self._cache.pop(conn_id, None)

    async def delete(self, conn_id: UUID) -> None:
        """Delete a connection and evict it from cache."""
        await self._db.execute(
            "DELETE FROM data.external_connections WHERE id = :p0",
            conn_id,
        )
        resolved = self._cache.pop(conn_id, None)
        if resolved and resolved.mcp_client:
            await resolved.mcp_client.close()

    async def list(self, tenant_id: UUID) -> list[ConnectionRecord]:
        """List all connections for a tenant (credentials excluded)."""
        rows = await self._db.fetch(
            "SELECT id, tenant_id, name, type, config, health_status, "
            "last_health_check, created_at "
            "FROM data.external_connections WHERE tenant_id = :p0 ORDER BY created_at",
            tenant_id,
        )
        return [self._row_to_record(row) for row in rows]

    async def get(self, conn_id: UUID) -> ConnectionRecord | None:
        """Fetch a single connection by ID (credentials excluded)."""
        row = await self._db.fetch_one(
            "SELECT id, tenant_id, name, type, config, health_status, "
            "last_health_check, created_at "
            "FROM data.external_connections WHERE id = :p0",
            conn_id,
        )
        if row is None:
            return None
        return self._row_to_record(row)

    # -- Resolve ----------------------------------------------------------

    async def resolve(self, conn_id: UUID) -> ResolvedConnection:
        """Decrypt credentials and construct a live McpClient or HttpApiConnector.

        Results are cached in an LRU — repeated calls return the same instance
        without re-spawning subprocesses or re-creating HTTP clients.
        """
        cached = self._cache.get(conn_id)
        if cached is not None:
            self._cache.move_to_end(conn_id)
            return cached

        row = await self._db.fetch_one(
            "SELECT id, tenant_id, name, type, config, credentials_encrypted "
            "FROM data.external_connections WHERE id = :p0",
            conn_id,
        )
        if row is None:
            raise ValueError(f"connection not found: {conn_id}")

        name = str(row["name"])
        conn_type = str(row["type"])
        config = self._parse_config(row["config"])
        creds_encrypted = row.get("credentials_encrypted")
        credentials: dict[str, str] = {}
        if creds_encrypted:
            credentials = {
                k: str(v)
                for k, v in self._crypto.decrypt(creds_encrypted).items()
            }

        resolved = self._build_resolved(conn_id, name, conn_type, config, credentials)
        self._cache[conn_id] = resolved
        self._cache.move_to_end(conn_id)
        if len(self._cache) > self._lru_size:
            _, evicted = self._cache.popitem(last=False)
            if evicted.mcp_client:
                await evicted.mcp_client.close()
        return resolved

    # -- Health check -----------------------------------------------------

    async def health_check(self, conn_id: UUID) -> HealthStatus:
        """Run a health check on a single connection."""
        try:
            resolved = await self.resolve(conn_id)
            healthy = False
            if resolved.mcp_client:
                healthy = await resolved.mcp_client.health_check()
            elif resolved.http_connector:
                healthy = await resolved.http_connector.health_check()

            status = "healthy" if healthy else "unhealthy"
            await self._db.execute(
                "UPDATE data.external_connections "
                "SET health_status = :p1, last_health_check = now(), updated_at = now() "
                "WHERE id = :p0",
                conn_id,
                status,
            )
            return HealthStatus(status=status)
        except Exception as exc:
            logger.warning("health check failed for %s: %s", conn_id, exc)
            await self._db.execute(
                "UPDATE data.external_connections "
                "SET health_status = 'unhealthy', last_health_check = now(), "
                "updated_at = now() WHERE id = :p0",
                conn_id,
            )
            return HealthStatus(status="unhealthy", error=str(exc))

    async def health_check_all(self, tenant_id: UUID) -> dict[UUID, HealthStatus]:
        """Run health checks for all connections of a tenant."""
        records = await self.list(tenant_id)
        results: dict[UUID, HealthStatus] = {}
        for record in records:
            results[record.id] = await self.health_check(record.id)
        return results

    # -- Internal helpers -------------------------------------------------

    def _build_resolved(
        self,
        conn_id: UUID,
        name: str,
        conn_type: str,
        config: dict[str, Any],
        credentials: dict[str, str],
    ) -> ResolvedConnection:
        """Construct a ResolvedConnection based on type."""
        if conn_type == "mcp_stdio":
            command = str(config.get("command", ""))
            args = [str(a) for a in config.get("args", [])]
            env = {k: str(v) for k, v in config.get("env", {}).items()}
            transport = StdioTransport(command=command, args=args, env=env)
            client = McpClient(server_name=name, transport=transport)
            return ResolvedConnection(
                conn_id=conn_id, name=name, type=conn_type, mcp_client=client
            )

        if conn_type == "http_api":
            spec = self._build_http_spec(config)
            auth = self._build_http_auth(config, credentials)
            http_client = httpx.AsyncClient(timeout=spec.default_timeout)
            connector = HttpApiConnector(
                spec=spec,
                auth=auth,
                http_client=http_client,
                credentials=credentials,
            )
            return ResolvedConnection(
                conn_id=conn_id, name=name, type=conn_type, http_connector=connector
            )

        raise ValueError(f"unsupported connection type for resolve: {conn_type}")

    @staticmethod
    def _build_http_spec(config: dict[str, Any]) -> HttpApiSpec:
        """Construct HttpApiSpec from connection config."""
        spec_data = config.get("spec", config)
        resources: dict[str, ResourceSpec] = {}
        for res_name, res_data in spec_data.get("resources", {}).items():
            resources[res_name] = ResourceSpec(
                path=str(res_data.get("path", "")),
                methods=list(res_data.get("methods", ["GET"])),
                id_field=str(res_data.get("id_field", "id")),
                schema=dict(res_data.get("schema", {})),
                access_mode=str(res_data.get("access_mode", "read_write")),
            )
        pagination = None
        pag_data = spec_data.get("pagination")
        if pag_data:
            pagination = PaginationSpec(**pag_data)
        return HttpApiSpec(
            base_url=str(spec_data.get("base_url", "")),
            resources=resources,
            health_check_path=spec_data.get("health_check_path"),
            default_timeout=float(spec_data.get("default_timeout", 30.0)),
            pagination=pagination,
        )

    @staticmethod
    def _build_http_auth(
        config: dict[str, Any], credentials: dict[str, str]
    ) -> HttpAuth:
        """Construct HttpAuth from config + credentials."""
        auth_data = config.get("auth", {})
        return HttpAuth(
            type=str(auth_data.get("type", "api_key")),
            token_endpoint=auth_data.get("token_endpoint"),
            header_name=str(auth_data.get("header_name", "Authorization")),
            header_prefix=str(auth_data.get("header_prefix", "Bearer")),
        )

    @staticmethod
    def _parse_config(raw: Any) -> dict[str, Any]:
        """Parse config from DB row — handles both str and dict."""
        if isinstance(raw, str):
            result: dict[str, Any] = json.loads(raw)
            return result
        if isinstance(raw, dict):
            return raw
        return {}

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> ConnectionRecord:
        """Convert a DB row to a ConnectionRecord (no credentials)."""
        return ConnectionRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=str(row["name"]),
            type=str(row["type"]),
            config=ConnectionManager._parse_config(row["config"]),
            health_status=str(row.get("health_status", "unknown")),
            last_health_check=row.get("last_health_check"),
            created_at=row.get("created_at"),
        )
