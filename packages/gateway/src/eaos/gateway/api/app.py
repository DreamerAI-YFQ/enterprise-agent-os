"""FastAPI application factory — health, ready, and unified error handling.

create_app(config) wires CORS + JWT auth middleware, registers health
endpoints, adds unified ``{detail, code}`` exception handlers, and includes
all available route modules via ``_try_include_routers``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eaos.core.auth import status_to_code  # noqa: TC002
from eaos.core.config import AppConfig
from eaos.gateway.api.middleware import setup_middleware
from fastapi import FastAPI, HTTPException, Request  # noqa: TC003
from fastapi.exceptions import RequestValidationError  # noqa: TC003
from fastapi.responses import JSONResponse  # noqa: TC003
from fastapi.staticfiles import StaticFiles


def create_app(config: AppConfig) -> FastAPI:
    """Build a FastAPI application with auth middleware and health routes."""
    app = FastAPI(
        title="EAOS API",
        version="0.1.0",
        docs_url="/docs" if config.debug else None,
        redoc_url=None,
    )
    app.state.config = config

    setup_middleware(app, config)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(  # noqa: RUF029
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": status_to_code(exc.status_code)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(  # noqa: RUF029
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors(), "code": "validation_error"},
        )

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    _try_include_routers(app)
    _mount_uploads(app, config)

    return app


def _mount_uploads(app: FastAPI, config: AppConfig) -> None:
    """Mount /uploads as a static file directory, creating it if missing."""
    uploads_dir = Path(config.uploads.dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")


def _try_include_routers(app: FastAPI) -> None:
    """Include T13/T14 routers if the modules exist (graceful no-op otherwise)."""
    for module_path, attr in (
        ("eaos.gateway.api.routes.auth", "router"),
        ("eaos.gateway.api.routes.agents", "router"),
        ("eaos.gateway.api.routes.sessions", "router"),
        ("eaos.gateway.api.routes.tasks", "router"),
        ("eaos.gateway.api.routes.notifications", "router"),
        ("eaos.gateway.api.routes.knowledge", "router"),
        ("eaos.gateway.api.routes.skills", "router"),
        ("eaos.gateway.api.routes.memory", "router"),
        ("eaos.gateway.api.routes.bi", "router"),
        ("eaos.gateway.api.routes.knowledge_docs", "router"),
        ("eaos.gateway.api.routes.contributions", "router"),
        ("eaos.gateway.api.routes.ontology", "router"),
        ("eaos.gateway.api.routes.me", "router"),
        ("eaos.gateway.api.routes.metrics", "router"),
        ("eaos.gateway.api.routes.users", "router"),
        ("eaos.gateway.api.routes.departments", "router"),
        ("eaos.gateway.api.routes.config", "router"),
        ("eaos.gateway.api.routes.invoke", "router"),
        ("eaos.gateway.api.routes.webhook", "router"),
        ("eaos.gateway.api.routes.admin", "router"),
        ("eaos.gateway.api.routes.evolution", "router"),
        ("eaos.gateway.api.routes.safety_cases", "router"),
        ("eaos.gateway.api.routes.connections", "router"),
        ("eaos.gateway.api.routes.upload", "router"),
        ("eaos.gateway.api.routes.data_management", "router"),
        ("eaos.gateway.api.routes.roles", "router"),
        ("eaos.gateway.api.routes.super_admin", "router"),
        ("eaos.gateway.api.routes.sso", "router"),
    ):
        try:
            import importlib

            mod = importlib.import_module(module_path)
            router = getattr(mod, attr)
            app.include_router(router)
        except ImportError:
            pass


def create_test_app(
    *,
    secret: str = "test-secret",
    runner: Any = None,
    orchestrator: Any = None,
    gateway: Any = None,
    tracer: Any = None,
    harness: Any = None,
) -> FastAPI:
    """Build a minimal app for unit tests with optional DI stubs."""
    config = AppConfig(secret_key=secret, debug=True)  # type: ignore[call-arg]
    app = create_app(config)
    app.state.runner = runner
    app.state.orchestrator = orchestrator
    app.state.gateway = gateway
    app.state.tracer = tracer
    app.state.harness = harness
    return app
