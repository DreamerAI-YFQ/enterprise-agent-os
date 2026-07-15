"""Root pytest configuration.

Integration tests require live PG/Redis services. They are skipped by
default; set ``EAOS_RUN_INTEGRATION=1`` to opt in.

The ``live_stack`` fixture (session-scoped) assembles the full M7
tool-execution stack against the live ``mock_saas`` service (REST on
:18000 + MCP stdio subprocess) and a migrated PG instance. It only
activates when an integration test requests it.
"""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

    from eaos.data.connection_manager import ConnectionManager
    from eaos.data.http_connector import HttpApiConnector
    from eaos.data.mcp.client import McpClient
    from eaos.data.mcp.registry import ToolRegistry
    from eaos.data.text2sql.sandbox import PgSqlSandbox
    from eaos.harness.evolution.approval import ApprovalGateImpl
    from eaos.harness.write_pipeline import WritePipeline
    from eaos.infra.db.base import DbClient
    from eaos.observability.audit import AuditLogger

MOCK_SAAS_BASE_URL = "http://localhost:18000"
MOCK_SAAS_API_KEY = "eaos-api-key-001"
# Matches EAOS_APP__SECRET_KEY in .env so CredentialCrypto derives the same key.
EAOS_SECRET_KEY = "dev-secret-change-in-prod"


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    # LLM tests require an explicit opt-in AND a real API key; skip otherwise.
    if os.environ.get("EAOS_RUN_LLM") != "1":
        skip_llm = pytest.mark.skip(
            reason="llm test; set EAOS_RUN_LLM=1 and EAOS_LLM__OPENAI_API_KEY to run"
        )
        for item in items:
            if "llm" in item.keywords:
                item.add_marker(skip_llm)

    if os.environ.get("EAOS_RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(
        reason="integration test; set EAOS_RUN_INTEGRATION=1 to run"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def tenant_id() -> UUID:
    """The seed tenant UUID (matches seed.py TENANT_ID)."""
    from uuid import UUID

    return UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="session")
async def db() -> AsyncGenerator[DbClient, None]:
    """Session-scoped PgClient for integration tests.

    Requires EAOS_DB__URL (or default localhost:5432/eaos) pointing at a
    migrated + seeded database.
    """
    from eaos.core.config import AppConfig
    from eaos.infra.db.postgres import PgClient

    config = AppConfig()
    client = PgClient(config.db)
    yield client
    await client._engine.dispose()


@dataclass
class LiveStack:
    """Bundle of live components for M7 end-to-end tests."""

    db: DbClient
    tenant_id: UUID
    connection_manager: ConnectionManager
    http_connector: HttpApiConnector
    mcp_client: McpClient
    http_conn_id: UUID
    mcp_conn_id: UUID
    tool_registry: ToolRegistry
    audit_logger: AuditLogger
    approval_gate: ApprovalGateImpl
    write_pipeline: WritePipeline
    sandbox: PgSqlSandbox
    harness: Any  # mock; guard/post_guard are no-ops


def _mock_saas_http_config() -> dict[str, Any]:
    """Static config describing the mock_saas REST surface."""
    return {
        "base_url": MOCK_SAAS_BASE_URL,
        "health_check_path": "/health",
        "default_timeout": 15.0,
        "resources": {
            "orders": {
                "path": "/api/v1/orders/{id}",
                "methods": ["GET", "POST", "PUT", "DELETE"],
                "id_field": "id",
                "access_mode": "read_write",
            },
            "customers": {
                "path": "/api/v1/customers/{id}",
                "methods": ["GET", "POST", "PUT", "DELETE"],
                "id_field": "id",
                "access_mode": "read_write",
            },
            "inventory": {
                "path": "/api/v1/inventory/{id}",
                "methods": ["GET", "PUT"],
                "id_field": "sku",
                "access_mode": "read_write",
            },
        },
        "pagination": {
            "type": "page",
            "page_param": "page",
            "page_size_param": "page_size",
            "data_field": "data",
            "total_field": "total",
        },
        "auth": {
            "type": "api_key",
            "header_name": "X-API-Key",
            "header_prefix": "",
        },
    }


def _mock_saas_mcp_config() -> dict[str, Any]:
    """Config for launching the mock_saas MCP server as a stdio subprocess."""
    return {
        "command": sys.executable,
        "args": ["-m", "mock_saas.mcp_server"],
        "env": {},
    }


@pytest.fixture(scope="session")
async def live_stack(
    db: DbClient, tenant_id: UUID
) -> AsyncGenerator[LiveStack, None]:
    """Assemble the full M7 tool-execution stack against live mock_saas + PG."""
    from eaos.data.connection_manager import ConnectionManager
    from eaos.data.connection_types import ConnectionSpec
    from eaos.data.crypto import CredentialCrypto
    from eaos.data.mcp.registry import ToolRegistry
    from eaos.data.text2sql.sandbox import PgSqlSandbox
    from eaos.harness.evolution.approval import ApprovalGateImpl
    from eaos.harness.write_pipeline import WritePipeline
    from eaos.observability.audit import AuditLogger

    crypto = CredentialCrypto(EAOS_SECRET_KEY)
    cm = ConnectionManager(db, crypto)

    # Idempotent setup: delete any leftover connections with our names so
    # re-runs don't hit the (tenant_id, name) unique constraint.
    for existing in await cm.list(tenant_id):
        if existing.name in ("mock-saas-http", "mock-saas"):
            await cm.delete(existing.id)

    http_conn_id = await cm.register(
        ConnectionSpec(
            tenant_id=tenant_id,
            name="mock-saas-http",
            type="http_api",
            config=_mock_saas_http_config(),
            credentials={"api_key": MOCK_SAAS_API_KEY},
        )
    )
    mcp_conn_id = await cm.register(
        ConnectionSpec(
            tenant_id=tenant_id,
            name="mock-saas",
            type="mcp_stdio",
            config=_mock_saas_mcp_config(),
            credentials=None,
        )
    )

    http_resolved = await cm.resolve(http_conn_id)
    mcp_resolved = await cm.resolve(mcp_conn_id)
    assert http_resolved.http_connector is not None, "http_connector not built"
    assert mcp_resolved.mcp_client is not None, "mcp_client not built"
    http_connector = http_resolved.http_connector
    mcp_client = mcp_resolved.mcp_client

    registry = ToolRegistry()
    registry.register_mcp("mock-saas", mcp_client)
    registry.register_internal("mock_saas", http_connector)

    audit_logger = AuditLogger(db)
    approval_gate = ApprovalGateImpl(db)

    # Mock harness: guard/post_guard are no-ops so the real WritePipeline
    # orchestration (the logic under test) runs end-to-end.
    harness: Any = MagicMock()
    harness.guard = AsyncMock(return_value=None)
    harness.post_guard = AsyncMock(side_effect=lambda ctx, result: result)

    def _resolver(_tool_name: str) -> Any:
        # All HTTP write intents route to the mock_saas HTTP connector.
        return http_connector

    pipeline = WritePipeline(harness, _resolver, audit_logger, approval_gate)
    sandbox = PgSqlSandbox(db)

    stack = LiveStack(
        db=db,
        tenant_id=tenant_id,
        connection_manager=cm,
        http_connector=http_connector,
        mcp_client=mcp_client,
        http_conn_id=http_conn_id,
        mcp_conn_id=mcp_conn_id,
        tool_registry=registry,
        audit_logger=audit_logger,
        approval_gate=approval_gate,
        write_pipeline=pipeline,
        sandbox=sandbox,
        harness=harness,
    )
    yield stack
    # Teardown: terminate MCP subprocess + close HTTP client. MCP's anyio
    # cancel scope is task-bound and may raise if torn down from a different
    # task than setup (pytest-asyncio session fixture) — best-effort close.
    with contextlib.suppress(Exception):
        await mcp_client.close()
    await http_connector._client.aclose()
