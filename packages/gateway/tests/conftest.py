"""Gateway-test compatibility for DB-backed JWT identity validation.

Most legacy route tests use an unconfigured ``AsyncMock`` only to exercise a
route after authentication.  Production and the dedicated auth tests always
use the real DB-backed evaluator; this fixture supplies a claims-shaped test
identity only when a legacy mock has no identity contract at all.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import jwt
import pytest
from eaos.core.auth import PermissionEvaluator, Principal


class _LegacyRouteIdentityEvaluator(PermissionEvaluator):
    def __init__(self, role: str) -> None:
        self._role = role

    async def load_active_identity(self, user_id: Any, tenant_id: Any) -> Principal | None:
        return Principal(user_id=user_id, tenant_id=tenant_id, role=self._role)


def _token_role(scope: dict[str, Any]) -> str | None:
    headers = scope.get("headers", [])
    authorization = next(
        (value.decode("utf-8") for key, value in headers if key.lower() == b"authorization"),
        "",
    )
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except Exception:  # noqa: BLE001 - test-only compatibility path
        return None
    role = payload.get("role")
    return role if isinstance(role, str) and role else None


@pytest.fixture(autouse=True)
def _legacy_route_identity_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from eaos.gateway.api import middleware as middleware_module

    original = middleware_module._resolve_auth_evaluator

    def resolve(scope: dict[str, Any]) -> PermissionEvaluator | None:
        app = scope.get("app")
        state = getattr(app, "state", None)
        configured = getattr(state, "auth_evaluator", None)
        if isinstance(configured, PermissionEvaluator):
            return configured
        db = getattr(state, "db", None)
        validates_identity = bool(
            isinstance(db, Mock) and vars(db).get("_eaos_validate_identity", False)
        )
        if db is not None and (not isinstance(db, Mock) or validates_identity):
            return original(scope)
        role = _token_role(scope)
        return _LegacyRouteIdentityEvaluator(role) if role is not None else None

    monkeypatch.setattr(middleware_module, "_resolve_auth_evaluator", resolve)
