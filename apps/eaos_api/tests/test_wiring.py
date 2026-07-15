"""T1 wiring tests — build_deps, lifespan, /health via ASGITransport.

Integration tests require a live PostgreSQL (migrated + seeded) because
build_deps constructs PgClient and DB-backed repositories. Set
``EAOS_RUN_INTEGRATION=1`` to run them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from eaos.infra.db.base import DbClient

pytestmark = pytest.mark.integration


@pytest.fixture
async def deps(
    db: DbClient, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[Any, None]:
    """Build AppDeps against the integration-test PG; close on teardown.

    The shared ``db`` fixture is only used to ensure the test PG is reachable;
    build_deps creates its own PgClient (closed via close_deps).
    """
    from eaos.core.config import AppConfig
    from eaos_api.wiring import build_deps, close_deps

    # Force null embedder + no LLM adapters so build_deps doesn't need API keys.
    monkeypatch.delenv("EAOS_LLM__OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EAOS_EMBEDDING__API_KEY", raising=False)
    config = AppConfig.load_config(env_file=None)
    built = await build_deps(config)
    try:
        yield built
    finally:
        await close_deps(built)


class TestBuildDeps:
    async def test_all_components_non_none(self, deps: Any) -> None:
        assert deps.db is not None
        assert deps.llm is not None
        assert deps.runner is not None
        assert deps.orchestrator is not None
        assert deps.gateway is not None
        assert deps.tracer is not None
        assert deps.harness is not None
        assert deps.knowledge_engine is not None
        assert deps.evolution_pipeline is not None
        assert deps.trainer is not None
        assert deps.dataset_builder is not None
        assert deps.trace_query is not None
        assert deps.policy_engine is not None
        assert deps.cost_governor is not None
        assert deps.approval_gate is not None
        assert deps.ambient_monitor is not None
        assert deps.auth_evaluator is not None

    async def test_db_is_usable(self, deps: Any) -> None:
        """The wired PgClient can actually query the DB."""
        rows = await deps.db.fetch("SELECT 1 AS one")
        assert rows[0]["one"] == 1


class TestLifespan:
    async def test_lifespan_populates_state_and_closes(
        self, db: DbClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eaos.core.config import AppConfig
        from eaos.gateway.api.app import create_app
        from eaos_api.lifespan import lifespan

        monkeypatch.delenv("EAOS_LLM__OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("EAOS_EMBEDDING__API_KEY", raising=False)
        config = AppConfig.load_config(env_file=None)
        app = create_app(config)
        app.router.lifespan_context = lifespan

        # Drive the lifespan context manager directly. ASGITransport does not
        # run lifespan events; invoking the context manager exercises startup
        # (build_deps) and shutdown (close_deps) explicitly.
        async with lifespan(app):
            assert app.state.runner is not None
            assert app.state.harness is not None
            assert app.state.evolution_pipeline is not None
            assert app.state.tracer is not None
            assert app.state.gateway is not None


class TestMainApp:
    async def test_health_via_main_module(
        self, db: DbClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``eaos_api.main:app`` is importable and serves /health."""
        from httpx import ASGITransport, AsyncClient

        # Force a fresh import so module-level config picks up env.
        monkeypatch.delenv("EAOS_LLM__OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("EAOS_EMBEDDING__API_KEY", raising=False)

        import importlib

        import eaos_api.main as main_module

        importlib.reload(main_module)
        app = main_module.app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")
        assert response.status_code == 200
