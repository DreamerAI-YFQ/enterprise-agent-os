"""Core authentication — JWT middleware, Principal, RBAC+ABAC permission evaluator.

The middleware decodes JWT Bearer tokens (HS256) into a Principal and injects
tenant_id/user_id into contextvars for downstream tracing/logging. Service
accounts bypass JWT via EAOS_SERVICE_TOKEN.

PermissionEvaluator queries iam.permissions (RBAC matrix) and iam.memberships
(ABAC department scope). Admin role short-circuits to allow-all.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from eaos.core.context import tenant_id_var, user_id_var

if TYPE_CHECKING:
    from uuid import UUID


_PASSWORD_SCHEME = "scrypt"
_PASSWORD_VERSION = "v1"
_PASSWORD_N = 2**14
_PASSWORD_R = 8
_PASSWORD_P = 1
_PASSWORD_DKLEN = 32
_PASSWORD_SALT_BYTES = 16
_PASSWORD_MAX_BYTES = 4096
_PASSWORD_MAXMEM = 64 * 1024 * 1024
_DUMMY_SALT = b"eaos-auth-dummy!"
_DUMMY_DIGEST = bytes(_PASSWORD_DKLEN)


def _password_bytes(password: str) -> bytes:
    """Encode and bound a password before the deliberately expensive KDF."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    encoded = password.encode("utf-8")
    if len(encoded) > _PASSWORD_MAX_BYTES:
        raise ValueError("password is too long")
    return encoded


def _derive_password(password: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password,
        salt=salt,
        n=_PASSWORD_N,
        r=_PASSWORD_R,
        p=_PASSWORD_P,
        maxmem=_PASSWORD_MAXMEM,
        dklen=_PASSWORD_DKLEN,
    )


def hash_password(password: str) -> str:
    """Hash a password with stdlib scrypt and a fresh random salt.

    The self-describing format is::

        scrypt$v1$16384$8$1$<base64-salt>$<base64-derived-key>

    Parameters are deliberately fixed for this version.  A later cost change
    should introduce a new version instead of accepting attacker-controlled
    KDF parameters during verification.
    """
    password_bytes = _password_bytes(password)
    salt = secrets.token_bytes(_PASSWORD_SALT_BYTES)
    digest = _derive_password(password_bytes, salt)
    salt_text = base64.b64encode(salt).decode("ascii")
    digest_text = base64.b64encode(digest).decode("ascii")
    return (
        f"{_PASSWORD_SCHEME}${_PASSWORD_VERSION}${_PASSWORD_N}$"
        f"{_PASSWORD_R}${_PASSWORD_P}${salt_text}${digest_text}"
    )


def _parse_password_hash(encoded_hash: str) -> tuple[bytes, bytes] | None:
    if not isinstance(encoded_hash, str):
        return None
    parts = encoded_hash.split("$")
    if len(parts) != 7:
        return None
    scheme, version, n_text, r_text, p_text, salt_text, digest_text = parts
    if (
        scheme != _PASSWORD_SCHEME
        or version != _PASSWORD_VERSION
        or n_text != str(_PASSWORD_N)
        or r_text != str(_PASSWORD_R)
        or p_text != str(_PASSWORD_P)
    ):
        return None
    try:
        salt = base64.b64decode(salt_text, validate=True)
        digest = base64.b64decode(digest_text, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(salt) != _PASSWORD_SALT_BYTES or len(digest) != _PASSWORD_DKLEN:
        return None
    return salt, digest


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a password without raising and fail closed on malformed hashes.

    Even a malformed stored value performs one fixed-cost scrypt derivation,
    so missing or legacy credentials do not create an obvious cheap timing
    path.  The derived key comparison uses ``hmac.compare_digest``.
    """
    try:
        password_bytes = _password_bytes(password)
    except (TypeError, UnicodeError, ValueError):
        return False

    parsed = _parse_password_hash(encoded_hash)
    valid_format = parsed is not None
    salt, expected = parsed or (_DUMMY_SALT, _DUMMY_DIGEST)
    try:
        actual = _derive_password(password_bytes, salt)
    except (MemoryError, OverflowError, ValueError):
        return False
    matches = hmac.compare_digest(actual, expected)
    return valid_format and matches


# ASGI type aliases (kept loose to avoid starlette import in core).
Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

WHITELIST_PATHS: tuple[str, ...] = (
    "/health",
    "/ready",
    "/auth/login",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
)
WHITELIST_PREFIXES: tuple[str, ...] = (
    "/webhook/",
    "/uploads/",
    "/auth/sso/",
)


class AuthDb(Protocol):
    """Minimal DB interface for auth queries (subset of DbClient)."""

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]: ...

    async def fetch_one(self, sql: str, *params: Any) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class Principal:
    """Authenticated identity extracted from JWT or service token."""

    user_id: UUID
    tenant_id: UUID
    role: str
    departments: list[UUID] = field(default_factory=list)
    is_service_account: bool = False


class PermissionEvaluator:
    """RBAC + ABAC permission evaluator backed by iam.permissions/memberships.

    RBAC: role × resource × action matrix in iam.permissions.
    ABAC: per-permission constraint (scope=own, dept=true) evaluated against
    resource ownership / department membership.
    Admin role short-circuits to allow-all (no DB query).
    """

    def __init__(self, db: AuthDb) -> None:
        self._db = db

    async def load_departments(self, user_id: UUID) -> list[UUID]:
        """Fetch department IDs for a user from iam.memberships."""
        rows = await self._db.fetch(
            "SELECT department_id FROM iam.memberships WHERE user_id = :p0",
            user_id,
        )
        return [row["department_id"] for row in rows if row.get("department_id")]

    async def load_active_identity(self, user_id: UUID, tenant_id: UUID) -> Principal | None:
        """Resolve current DB-backed identity state for a JWT subject.

        JWT role claims are only historical assertions. Every authenticated
        request rechecks the user and tenant status and loads the current role
        so suspension, deletion, and role changes take effect immediately.
        Database failures intentionally propagate for the middleware to deny
        the request rather than trusting stale claims.
        """
        row = await self._db.fetch_one(
            "SELECT u.role, u.status, t.status AS tenant_status "
            "FROM iam.users u "
            "JOIN iam.tenants t ON t.id = u.tenant_id "
            "WHERE u.id = :p0 AND u.tenant_id = :p1",
            user_id,
            tenant_id,
        )
        if row is None:
            return None
        if row.get("status") != "active":
            return None
        if row.get("tenant_status", "active") != "active":
            return None
        role = row.get("role")
        if not isinstance(role, str) or not role:
            return None
        departments = await self.load_departments(user_id)
        return Principal(
            user_id=user_id,
            tenant_id=tenant_id,
            role=role,
            departments=departments,
        )

    async def check(
        self,
        principal: Principal,
        resource: str,
        action: str,
        *,
        resource_owner_id: UUID | None = None,
        resource_dept_id: UUID | None = None,
    ) -> bool:
        """Check whether principal may perform action on resource.

        Returns True if allowed, False otherwise. Never raises on denial —
        callers raise PermissionDeniedError when appropriate.
        """
        if principal.is_service_account:
            return True
        if principal.role in ("admin", "super_admin"):
            return True

        row = await self._db.fetch_one(
            'SELECT "constraint" FROM iam.permissions '
            "WHERE tenant_id = :p0 AND role = :p1 "
            "AND resource = :p2 AND action = :p3",
            principal.tenant_id,
            principal.role,
            resource,
            action,
        )
        if row is None:
            return False

        constraint = row.get("constraint")
        if constraint is None:
            return True

        if not isinstance(constraint, dict):
            return True

        scope = constraint.get("scope")
        if scope == "own":
            if resource_owner_id is None:
                return False
            return resource_owner_id == principal.user_id

        if constraint.get("dept"):
            if resource_dept_id is None:
                return False
            return resource_dept_id in principal.departments

        return True


_evaluator: PermissionEvaluator | None = None


def set_global_auth(evaluator: PermissionEvaluator) -> None:
    """Register the global PermissionEvaluator for @guarded / harness use."""
    global _evaluator
    _evaluator = evaluator


def get_global_auth() -> PermissionEvaluator | None:
    """Get the registered PermissionEvaluator, if any."""
    return _evaluator


def _decode_bearer(authorization: str) -> str | None:
    """Extract token from 'Bearer <token>' header value."""
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


STATUS_TO_CODE: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    500: "internal_error",
}


def status_to_code(status: int) -> str:
    """Map an HTTP status code to a machine-readable error code string."""
    return STATUS_TO_CODE.get(status, "error")


def _json_response(
    send: Send,
    status: int,
    detail: str,
) -> Awaitable[None]:
    """Send a JSON error response with unified ``{detail, code}`` shape."""

    async def _send() -> None:
        body = json.dumps({"detail": detail, "code": status_to_code(status)}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    coro: Awaitable[None] = _send()
    return coro


def _is_whitelisted(path: str) -> bool:
    if path in WHITELIST_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in WHITELIST_PREFIXES)


class JWTAuthMiddleware:
    """ASGI middleware: parse Bearer JWT → Principal → contextvars.

    Whitelisted paths (/health, /ready, /webhook/*) skip auth.
    Service accounts authenticate via EAOS_SERVICE_TOKEN env var.
    """

    def __init__(
        self,
        app: Any,
        secret: str,
        evaluator: PermissionEvaluator | None = None,
        evaluator_provider: Callable[[Scope], PermissionEvaluator | None] | None = None,
    ) -> None:
        self._app = app
        self._secret = secret
        self._evaluator = evaluator
        self._evaluator_provider = evaluator_provider

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if _is_whitelisted(path):
            await self._app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        authorization = _find_header(headers, b"authorization")
        if authorization is None:
            await _json_response(send, 401, "missing authorization header")
            return

        token = _decode_bearer(authorization.decode())
        if token is None:
            await _json_response(send, 401, "invalid authorization scheme")
            return

        principal = await self._authenticate(token, scope)
        if principal is None:
            await _json_response(send, 401, "invalid token")
            return

        scope["principal"] = principal
        tenant_id_var.set(principal.tenant_id)
        user_id_var.set(principal.user_id)

        await self._app(scope, receive, send)

    async def _authenticate(self, token: str, scope: Scope) -> Principal | None:
        """Resolve token to Principal (service token or JWT)."""
        service_token = os.environ.get("EAOS_SERVICE_TOKEN")
        if service_token and hmac.compare_digest(token, service_token):
            return _service_principal()

        import jwt

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
            )
        except Exception:
            return None

        evaluator = self._evaluator
        if evaluator is None and self._evaluator_provider is not None:
            try:
                evaluator = self._evaluator_provider(scope)
            except Exception:
                return None
        if evaluator is None:
            return None
        try:
            return await self._build_principal(payload, evaluator)
        except Exception:
            return None

    async def _build_principal(
        self,
        payload: dict[str, Any],
        evaluator: PermissionEvaluator,
    ) -> Principal | None:
        from uuid import UUID

        sub = payload.get("sub")
        tid = payload.get("tid")
        if sub is None or tid is None:
            return None

        try:
            user_id = UUID(str(sub))
            tenant_id = UUID(str(tid))
        except (ValueError, TypeError):
            return None

        return await evaluator.load_active_identity(user_id, tenant_id)


def _service_principal() -> Principal:
    """Build a system service-account Principal (tenant-scoped via env)."""
    from uuid import UUID

    tenant_env = os.environ.get("EAOS_SERVICE_TENANT_ID")
    tenant_id = UUID(tenant_env) if tenant_env else UUID(int=0)
    return Principal(
        user_id=UUID(int=0),
        tenant_id=tenant_id,
        role="admin",
        is_service_account=True,
    )


def _find_header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    """Case-insensitive header lookup."""
    lower = name.lower()
    for key, value in headers:
        if key.lower() == lower:
            return value
    return None


def create_jwt_token(
    secret: str,
    user_id: UUID,
    tenant_id: UUID,
    role: str,
    *,
    expires_in: int = 3600,
) -> str:
    """Mint a JWT token for testing / dev. Uses HS256."""
    from datetime import UTC, datetime, timedelta

    import jwt

    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "role": role,
        "exp": datetime.now(UTC) + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, secret, algorithm="HS256")
