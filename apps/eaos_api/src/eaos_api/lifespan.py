"""FastAPI lifespan — wires real dependencies at startup, disposes on shutdown.

The lifespan context manager builds the full AppDeps graph via
``wiring.build_deps`` and exposes each component on ``app.state`` so the
FastAPI dependency injectors in ``eaos.gateway.api.deps`` can resolve them.

``main.py`` installs this lifespan onto the app returned by ``create_app``
via ``app.router.lifespan_context`` so the gateway package's factory stays
untouched.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from eaos_api.wiring import AppDeps, build_deps, close_deps

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build deps at startup, stash on app.state, dispose on shutdown."""
    config = app.state.config
    deps: AppDeps = await build_deps(config)

    app.state.db = deps.db
    app.state.redis = deps.redis
    app.state.runner = deps.runner
    app.state.orchestrator = deps.orchestrator
    app.state.gateway = deps.gateway
    app.state.tracer = deps.tracer
    app.state.harness = deps.harness
    app.state.knowledge_engine = deps.knowledge_engine
    app.state.evolution_pipeline = deps.evolution_pipeline
    app.state.trainer = deps.trainer
    app.state.dataset_builder = deps.dataset_builder
    app.state.trace_query = deps.trace_query
    app.state.policy_engine = deps.policy_engine
    app.state.cost_governor = deps.cost_governor
    app.state.approval_gate = deps.approval_gate
    app.state.ambient_monitor = deps.ambient_monitor
    app.state.auth_evaluator = deps.auth_evaluator
    # F0: services for the new API routes
    app.state.dispatcher = deps.dispatcher
    app.state.tenant_manager = deps.tenant_manager
    app.state.skill_registry = deps.skill_registry
    app.state.skill_resolver = deps.skill_resolver
    app.state.memory_engine = deps.memory_engine
    app.state.rag_pipeline = deps.rag_pipeline
    app.state.ontology_repo = deps.ontology_repo
    app.state.connection_manager = deps.connection_manager
    app.state.sql_sandbox = deps.sql_sandbox
    app.state.text2sql_engine = deps.text2sql_engine
    app.state.data_connectors = deps.data_connectors
    app.state.tool_registry = deps.tool_registry
    app.state.write_pipeline = deps.write_pipeline
    app.state._deps = deps  # hold reference so close_deps can run on shutdown

    try:
        yield
    finally:
        await close_deps(deps)
