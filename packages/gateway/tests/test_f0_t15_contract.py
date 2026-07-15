"""F0-T15: API contract review — OpenAPI completeness + unified error format.

Verifies:
1. /openapi.json is accessible and contains a representative set of paths
2. Error responses use the unified ``{detail, code}`` shape across:
   - Auth middleware (401)
   - Route-level HTTPException (403, 404, 409)
   - Pydantic validation (422)
3. CORS headers are present on responses
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "f0-t15-contract-review-secret!"
TID = UUID("00000000-0000-0000-0000-000000000001")
ADMIN_ID = UUID("00000000-0000-0000-0000-000000000010")
EMP_ID = UUID("00000000-0000-0000-0000-000000000020")


def _config() -> AppConfig:
    return AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]


def _admin_token() -> str:
    return create_jwt_token(SECRET, ADMIN_ID, TID, "admin")


def _employee_token() -> str:
    return create_jwt_token(SECRET, EMP_ID, TID, "employee")


def _mock_db(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    single_row: dict[str, Any] | None = None,
    val: Any = 0,
) -> Any:
    db: Any = AsyncMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.fetch_one = AsyncMock(return_value=single_row)
    db.fetch_val = AsyncMock(return_value=val)
    db.execute = AsyncMock(return_value=None)
    return db


# ============================================================
# OpenAPI spec completeness
# ============================================================


class TestOpenApiSpec:
    """Verify /openapi.json is accessible and contains expected paths."""

    EXPECTED_PATHS = {
        "/health",
        "/ready",
        "/me",
        "/me/preferences",
        "/auth/login",
        "/auth/refresh",
        "/auth/logout",
        "/agents",
        "/agents/{agent_id}",
        "/admin/agents",
        "/sessions",
        "/sessions/{session_id}",
        "/sessions/{session_id}/messages",
        "/tasks",
        "/notifications",
        "/notifications/{notification_id}/read",
        "/knowledge/search",
        "/skills",
        "/skills/{skill_id}",
        "/admin/skills",
        "/bi/query",
        "/admin/bi/sql",
        "/admin/bi/tables",
        "/admin/knowledge/documents",
        "/admin/knowledge/documents/{document_id}",
        "/admin/ontology/terms",
        "/admin/ontology/terms/{term_id}",
        "/admin/ontology/gaps",
        "/memory",
        "/memory/{memory_id}",
        "/admin/metrics",
        "/admin/users",
        "/admin/users/{user_id}",
        "/admin/models",
        "/admin/plugins",
        "/admin/mcp/connectors",
        "/admin/report-templates",
        "/admin/report-templates/{template_id}",
        "/invoke",
        "/webhook/{channel}",
    }

    async def test_openapi_json_accessible(self) -> None:
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        assert spec["info"]["title"] == "EAOS API"
        assert "paths" in spec

    async def test_expected_paths_present(self) -> None:
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/openapi.json")
        spec = resp.json()
        actual_paths = set(spec["paths"].keys())
        missing = self.EXPECTED_PATHS - actual_paths
        assert not missing, f"Missing paths in OpenAPI spec: {missing}"

    async def test_openapi_has_at_least_50_paths(self) -> None:
        """The plan targets 57 endpoints; we should have at least 50 paths."""
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/openapi.json")
        spec = resp.json()
        assert len(spec["paths"]) >= 50, (
            f"Expected >=50 paths, got {len(spec['paths'])}"
        )


# ============================================================
# Unified error format: {detail, code}
# ============================================================


class TestUnifiedErrorFormat:
    """All error responses must use {detail, code} shape."""

    async def test_auth_middleware_401_has_code(self) -> None:
        """Auth middleware errors include 'code': 'unauthorized'."""
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/me")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data
        assert data["code"] == "unauthorized"

    async def test_route_403_has_code(self) -> None:
        """Route-level 403 (forbidden) includes 'code': 'forbidden'."""
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/admin/metrics",
                headers={"Authorization": f"Bearer {_employee_token()}"},
            )
        assert resp.status_code == 403
        data = resp.json()
        assert "detail" in data
        assert data["code"] == "forbidden"

    async def test_route_404_has_code(self) -> None:
        """Route-level 404 (not found) includes 'code': 'not_found'."""
        app = create_app(_config())
        app.state.db = _mock_db(single_row=None)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/admin/users/{uuid4()}",
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 404
        data = resp.json()
        assert "detail" in data
        assert data["code"] == "not_found"

    async def test_route_409_has_code(self) -> None:
        """Route-level 409 (conflict) includes 'code': 'conflict'."""
        app = create_app(_config())
        app.state.db = _mock_db(single_row={"id": uuid4()})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/admin/users",
                json={"email": "dup@test.com", "name": "Dup"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 409
        data = resp.json()
        assert "detail" in data
        assert data["code"] == "conflict"

    async def test_validation_422_has_code(self) -> None:
        """Pydantic validation errors include 'code': 'validation_error'."""
        app = create_app(_config())
        app.state.db = _mock_db()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Missing required 'email' field → 422
            resp = await client.post(
                "/admin/users",
                json={"name": "No Email"},
                headers={"Authorization": f"Bearer {_admin_token()}"},
            )
        assert resp.status_code == 422
        data = resp.json()
        assert data["code"] == "validation_error"
        assert isinstance(data["detail"], list)


# ============================================================
# CORS headers
# ============================================================


class TestCorsHeaders:
    """Verify CORS is configured for frontend cross-origin requests."""

    async def test_cors_allow_origin(self) -> None:
        """CORS allows cross-origin requests from the frontend dev server."""
        app = create_app(_config())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.options(
                "/health",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert resp.status_code == 200
        # With allow_credentials=True, Starlette reflects the origin
        allow_origin = resp.headers.get("access-control-allow-origin", "")
        assert allow_origin in ("*", "http://localhost:5173")
