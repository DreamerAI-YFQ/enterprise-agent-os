"""Regression tests for static-quality fixes with security semantics."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from eaos.core.auth import Principal
from eaos.gateway.api.routes import sso as sso_routes
from eaos.gateway.api.routes.super_admin import (
    delete_tenant,
    disable_tenant,
    enable_tenant,
)
from fastapi import FastAPI, HTTPException, Request

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from eaos.infra.db.base import DbClient


def _principal() -> Principal:
    return Principal(user_id=uuid4(), tenant_id=uuid4(), role="super_admin")


def _db() -> tuple[DbClient, AsyncMock]:
    mock = AsyncMock()
    return cast("DbClient", mock), mock


def _request(secret: str) -> Request:
    app = FastAPI()
    app.state.config = SimpleNamespace(secret_key=secret)
    scope: Any = {"type": "http", "app": app}
    return Request(scope)


async def test_delete_tenant_uses_returning_to_detect_missing_rows() -> None:
    db, db_mock = _db()
    tenant_id = uuid4()
    db_mock.fetch_one.return_value = {"id": tenant_id}

    await delete_tenant(tenant_id, _principal(), db)

    sql = db_mock.fetch_one.await_args.args[0]
    assert "DELETE FROM iam.tenants" in sql
    assert "RETURNING id" in sql
    db_mock.execute.assert_not_awaited()


async def test_delete_tenant_returns_not_found_when_no_row_is_deleted() -> None:
    db, db_mock = _db()
    db_mock.fetch_one.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await delete_tenant(uuid4(), _principal(), db)

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    ("operation", "status"),
    [
        (enable_tenant, "active"),
        (disable_tenant, "suspended"),
    ],
)
async def test_tenant_status_updates_use_returning(
    operation: Callable[..., Awaitable[dict[str, Any]]],
    status: str,
) -> None:
    db, db_mock = _db()
    tenant_id = uuid4()
    db_mock.fetch_one.return_value = {"id": tenant_id}

    result = await operation(tenant_id, _principal(), db)

    sql = db_mock.fetch_one.await_args.args[0]
    assert f"status = '{status}'" in sql
    assert "RETURNING id" in sql
    assert result == {"id": str(tenant_id), "status": status}


def test_sso_access_token_uses_the_application_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mint = MagicMock(return_value="signed-token")
    monkeypatch.setattr(sso_routes, "create_jwt_token", mint)
    user_id = uuid4()
    tenant_id = uuid4()

    token = sso_routes._create_access_token(
        _request("competition-secret"),
        user_id=user_id,
        tenant_id=tenant_id,
        role="employee",
    )

    assert token == "signed-token"
    mint.assert_called_once_with(
        "competition-secret",
        user_id=user_id,
        tenant_id=tenant_id,
        role="employee",
    )
