"""Tests for core auth — JWT, Principal, PermissionEvaluator, middleware."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from eaos.core.auth import (
    JWTAuthMiddleware,
    PermissionEvaluator,
    Principal,
    create_jwt_token,
    get_global_auth,
    set_global_auth,
)

SECRET = "test-secret-key"


class FakeAuthDb:
    """In-memory AuthDb for permission/membership queries."""

    def __init__(
        self,
        permissions: list[dict[str, Any]] | None = None,
        memberships: list[dict[str, Any]] | None = None,
    ) -> None:
        self._permissions = permissions or []
        self._memberships = memberships or []

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        if "iam.memberships" in sql:
            return list(self._memberships)
        return list(self._permissions)

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None:
        if "iam.permissions" in sql:
            for row in self._permissions:
                return row
            return None
        return None


class TestJwt:
    def test_encode_decode_roundtrip(self) -> None:
        uid = uuid4()
        tid = uuid4()
        token = create_jwt_token(SECRET, uid, tid, "employee")
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        assert payload["sub"] == str(uid)
        assert payload["tid"] == str(tid)
        assert payload["role"] == "employee"

    def test_expired_token_rejected(self) -> None:
        uid = uuid4()
        tid = uuid4()
        token = create_jwt_token(SECRET, uid, tid, "employee", expires_in=-10)
        with pytest.raises(jwt.ExpiredSignatureError):
            jwt.decode(token, SECRET, algorithms=["HS256"])

    def test_invalid_signature_rejected(self) -> None:
        uid = uuid4()
        tid = uuid4()
        token = create_jwt_token(SECRET, uid, tid, "employee")
        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(token, "wrong-secret", algorithms=["HS256"])


class TestPermissionEvaluator:
    def _principal(
        self,
        role: str = "employee",
        departments: list[Any] | None = None,
    ) -> Principal:
        return Principal(
            user_id=uuid4(),
            tenant_id=uuid4(),
            role=role,
            departments=departments or [],
        )

    async def test_admin_role_short_circuits(self) -> None:
        evaluator = PermissionEvaluator(FakeAuthDb())
        principal = self._principal(role="admin")
        assert await evaluator.check(principal, "agent", "delete") is True

    async def test_service_account_short_circuits(self) -> None:
        evaluator = PermissionEvaluator(FakeAuthDb())
        principal = Principal(
            user_id=uuid4(),
            tenant_id=uuid4(),
            role="employee",
            is_service_account=True,
        )
        assert await evaluator.check(principal, "agent", "delete") is True

    async def test_rbac_allow(self) -> None:
        evaluator = PermissionEvaluator(
            FakeAuthDb(permissions=[{"constraint": None}])
        )
        principal = self._principal(role="employee")
        assert await evaluator.check(principal, "agent", "read") is True

    async def test_rbac_deny_no_row(self) -> None:
        evaluator = PermissionEvaluator(FakeAuthDb(permissions=[]))
        principal = self._principal(role="employee")
        assert await evaluator.check(principal, "agent", "delete") is False

    async def test_abac_scope_own_owner_match(self) -> None:
        uid = uuid4()
        evaluator = PermissionEvaluator(
            FakeAuthDb(permissions=[{"constraint": {"scope": "own"}}])
        )
        principal = Principal(
            user_id=uid,
            tenant_id=uuid4(),
            role="employee",
        )
        assert await evaluator.check(
            principal, "agent", "read", resource_owner_id=uid
        ) is True

    async def test_abac_scope_own_owner_mismatch(self) -> None:
        evaluator = PermissionEvaluator(
            FakeAuthDb(permissions=[{"constraint": {"scope": "own"}}])
        )
        principal = self._principal(role="employee")
        assert (
            await evaluator.check(
                principal,
                "agent",
                "read",
                resource_owner_id=uuid4(),
            )
            is False
        )

    async def test_abac_dept_match(self) -> None:
        dept = uuid4()
        evaluator = PermissionEvaluator(
            FakeAuthDb(permissions=[{"constraint": {"dept": True}}])
        )
        principal = self._principal(role="employee", departments=[dept])
        assert (
            await evaluator.check(
                principal, "agent", "read", resource_dept_id=dept
            )
            is True
        )

    async def test_abac_dept_mismatch(self) -> None:
        evaluator = PermissionEvaluator(
            FakeAuthDb(permissions=[{"constraint": {"dept": True}}])
        )
        principal = self._principal(role="employee", departments=[uuid4()])
        assert (
            await evaluator.check(
                principal, "agent", "read", resource_dept_id=uuid4()
            )
            is False
        )

    async def test_load_departments(self) -> None:
        dept_id = uuid4()
        evaluator = PermissionEvaluator(
            FakeAuthDb(memberships=[{"department_id": dept_id}])
        )
        uid = uuid4()
        result = await evaluator.load_departments(uid)
        assert result == [dept_id]


class TestGlobalAuth:
    def test_set_and_get(self) -> None:
        evaluator = PermissionEvaluator(FakeAuthDb())
        set_global_auth(evaluator)
        assert get_global_auth() is evaluator

    def test_get_without_set_returns_none(self) -> None:
        set_global_auth(None)  # type: ignore[arg-type]
        assert get_global_auth() is None


class TestJWTAuthMiddleware:
    def _make_middleware(
        self,
        evaluator: PermissionEvaluator | None = None,
    ) -> tuple[
        JWTAuthMiddleware,
        AsyncMock,
        list[dict[str, Any]],
        Any,
        Any,
    ]:
        sent: list[dict[str, Any]] = []

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b""}

        app = AsyncMock()
        mw = JWTAuthMiddleware(app, SECRET, evaluator)
        return mw, app, sent, receive, send

    def _http_scope(
        self,
        path: str = "/invoke",
        token: str | None = None,
    ) -> dict[str, Any]:
        headers: list[tuple[bytes, bytes]] = []
        if token is not None:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        return {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": headers,
        }

    async def test_whitelist_health_passes(self) -> None:
        mw, app, _, receive, send = self._make_middleware()
        scope = self._http_scope(path="/health")
        await mw(scope, receive, send)
        app.assert_awaited_once()

    async def test_whitelist_webhook_passes(self) -> None:
        mw, app, _, receive, send = self._make_middleware()
        scope = self._http_scope(path="/webhook/dingtalk")
        await mw(scope, receive, send)
        app.assert_awaited_once()

    async def test_no_token_returns_401(self) -> None:
        mw, app, sent, receive, send = self._make_middleware()
        scope = self._http_scope(path="/invoke")
        await mw(scope, receive, send)
        app.assert_not_awaited()
        assert sent[0]["status"] == 401

    async def test_invalid_token_returns_401(self) -> None:
        mw, app, sent, receive, send = self._make_middleware()
        scope = self._http_scope(path="/invoke", token="garbage")
        await mw(scope, receive, send)
        app.assert_not_awaited()
        assert sent[0]["status"] == 401

    async def test_valid_token_calls_app_and_sets_principal(self) -> None:
        mw, app, _, receive, send = self._make_middleware()
        uid = uuid4()
        tid = uuid4()
        token = create_jwt_token(SECRET, uid, tid, "employee")
        scope = self._http_scope(path="/invoke", token=token)

        await mw(scope, receive, send)
        app.assert_awaited_once()
        principal = scope.get("principal")
        assert principal is not None
        assert isinstance(principal, Principal)
        assert principal.user_id == uid
        assert principal.tenant_id == tid
        assert principal.role == "employee"

    async def test_service_token_authenticates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EAOS_SERVICE_TOKEN", "svc-secret")
        mw, app, _, receive, send = self._make_middleware()
        scope = self._http_scope(path="/invoke", token="svc-secret")
        await mw(scope, receive, send)
        app.assert_awaited_once()
        principal = scope.get("principal")
        assert principal is not None
        assert principal.is_service_account is True

    async def test_non_http_scope_passes_through(self) -> None:
        mw, app, _, receive, send = self._make_middleware()
        scope: dict[str, Any] = {"type": "lifespan"}
        await mw(scope, receive, send)
        app.assert_awaited_once()
