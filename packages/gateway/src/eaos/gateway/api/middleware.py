"""Gateway ASGI middleware setup — CORS + JWT auth.

JWTAuthMiddleware is defined in eaos.core.auth (T2) and re-exported here for
gateway-local imports. setup_middleware() registers both CORS (outer) and JWT
(inner) on a FastAPI app so preflight OPTIONS bypass auth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eaos.core.auth import JWTAuthMiddleware, Principal
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

if TYPE_CHECKING:
    from eaos.core.auth import PermissionEvaluator
    from eaos.core.config import AppConfig
    from fastapi import FastAPI

__all__ = ["JWTAuthMiddleware", "Principal", "setup_middleware"]


class ApiPrefixMiddleware(BaseHTTPMiddleware):
    """Strip a leading /api prefix from incoming requests.

    The FastAPI backend has no /api prefix. Web mode uses Vite proxy to add
    then strip /api, but Tauri desktop builds may still request /api/* due to
    cached bundles or user input. This middleware makes /api/* and /* both
    work, avoiding the HTML 401 error that breaks the frontend JSON parser.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        scope = request.scope
        path: str = scope.get("path", "")
        if path.startswith("/api/"):
            scope["path"] = path[4:]
            scope["raw_path"] = scope["path"].encode("utf-8")
        elif path == "/api":
            scope["path"] = "/"
            scope["raw_path"] = b"/"
        return await call_next(request)


def setup_middleware(
    app: FastAPI,
    config: AppConfig,
    evaluator: PermissionEvaluator | None = None,
) -> None:
    """Register /api prefix stripper, CORS (outer) and JWT auth (inner).

    add_middleware inserts at position 0 (outermost), so the last call wins.
    ApiPrefixMiddleware is added last → outermost → rewrites path before CORS
    and JWT run.
    """
    app.add_middleware(
        JWTAuthMiddleware,
        secret=config.secret_key,
        evaluator=evaluator,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(ApiPrefixMiddleware)
