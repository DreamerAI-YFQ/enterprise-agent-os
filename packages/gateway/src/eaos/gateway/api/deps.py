"""FastAPI dependency injection — principal, runner, orchestrator, gateway, tracer, harness.

All singletons are stored on app.state at startup. get_principal() reads the
Principal injected by JWTAuthMiddleware into the ASGI scope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request  # noqa: TC002

if TYPE_CHECKING:
    from eaos.agent.dispatcher import AgentDispatcher
    from eaos.agent.memory.engine import MemoryEngine
    from eaos.agent.orchestrator import AgentOrchestrator
    from eaos.agent.runner import AgentRunner
    from eaos.agent.tenant import TenantManager
    from eaos.core.auth import Principal
    from eaos.data.connection_manager import ConnectionManager
    from eaos.data.connector import DataConnector
    from eaos.data.text2sql.engine import Text2SQLEngine
    from eaos.data.text2sql.sandbox import SqlSandbox
    from eaos.gateway.im.gateway import MessageGateway
    from eaos.harness.guard import HarnessGuard
    from eaos.infra.db.base import DbClient
    from eaos.infra.db.redis_base import RedisClient
    from eaos.knowledge.engine import KnowledgeEngine
    from eaos.knowledge.ontology.repository import OntologyRepository
    from eaos.knowledge.rag.pipeline import RAGPipeline
    from eaos.observability.tracer import Tracer
    from eaos.skills.registry import SkillRegistry
    from eaos.skills.resolver import SkillResolver


async def get_principal(request: Request) -> Principal:
    """Extract the authenticated Principal from the ASGI scope."""
    principal = request.scope.get("principal")
    if principal is None:
        from eaos.core.errors import EaosError

        raise EaosError("no principal in scope — auth middleware not wired")
    return principal  # type: ignore[no-any-return]


async def get_db(request: Request) -> DbClient:
    return request.app.state.db  # type: ignore[no-any-return]


async def get_redis(request: Request) -> RedisClient:
    """Get the Redis client singleton from app.state."""
    return request.app.state.redis  # type: ignore[no-any-return]


async def get_runner(request: Request) -> AgentRunner:
    return request.app.state.runner  # type: ignore[no-any-return]


async def get_orchestrator(request: Request) -> AgentOrchestrator:
    return request.app.state.orchestrator  # type: ignore[no-any-return]


async def get_gateway(request: Request) -> MessageGateway:
    return request.app.state.gateway  # type: ignore[no-any-return]


async def get_tracer(request: Request) -> Tracer:
    return request.app.state.tracer  # type: ignore[no-any-return]


async def get_harness(request: Request) -> HarnessGuard:
    return request.app.state.harness  # type: ignore[no-any-return]


async def get_dispatcher(request: Request) -> AgentDispatcher:
    return request.app.state.dispatcher  # type: ignore[no-any-return]


async def get_tenant_manager(request: Request) -> TenantManager:
    return request.app.state.tenant_manager  # type: ignore[no-any-return]


async def get_skill_registry(request: Request) -> SkillRegistry:
    return request.app.state.skill_registry  # type: ignore[no-any-return]


async def get_skill_resolver(request: Request) -> SkillResolver:
    return request.app.state.skill_resolver  # type: ignore[no-any-return]


async def get_memory_engine(request: Request) -> MemoryEngine:
    return request.app.state.memory_engine  # type: ignore[no-any-return]


async def get_rag_pipeline(request: Request) -> RAGPipeline:
    return request.app.state.rag_pipeline  # type: ignore[no-any-return]


async def get_ontology_repo(request: Request) -> OntologyRepository:
    return request.app.state.ontology_repo  # type: ignore[no-any-return]


async def get_connection_manager(request: Request) -> ConnectionManager:
    return request.app.state.connection_manager  # type: ignore[no-any-return]


async def get_knowledge_engine(request: Request) -> KnowledgeEngine:
    return request.app.state.knowledge_engine  # type: ignore[no-any-return]


async def get_sql_sandbox(request: Request) -> SqlSandbox:
    return request.app.state.sql_sandbox  # type: ignore[no-any-return]


async def get_text2sql_engine(request: Request) -> Text2SQLEngine:
    return request.app.state.text2sql_engine  # type: ignore[no-any-return]


async def get_data_connectors(request: Request) -> dict[str, DataConnector]:
    return request.app.state.data_connectors  # type: ignore[no-any-return]
