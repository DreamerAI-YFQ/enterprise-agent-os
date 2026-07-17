# Enterprise Agent OS

> Platform-level Agent governance system — enterprise-grade AI agent distribution, collaboration, and knowledge compounding platform.

## Quick Start

```bash
# Install dependencies
uv sync --all-packages

# Copy local configuration and start the required infrastructure
cp .env.example .env
docker compose up -d postgres redis mock-saas

# Run database migrations
uv run alembic upgrade head

# Seed a fresh local demo database (destructive: replaces existing demo data)
uv run python -m eaos.infra.db.seed

# Start API server
uv run uvicorn eaos_api.main:app --reload

# Run tests
uv run pytest
```

## Project Structure

```
enterprise-agent-os/
├── packages/           # 10 business packages (monorepo)
│   ├── core/           # Shared kernel: context, errors, config, events
│   ├── infra/          # Infrastructure adapters: DB, Redis, LLM, vector
│   ├── data/           # L1: MCP connectors, Text2SQL, SQL sandbox
│   ├── knowledge/      # L2: Ontology, RAG, organizational memory
│   ├── skills/         # L3: Skill marketplace, registry, executor
│   ├── agent/          # L4: LangGraph orchestration, multi-tenant, collaboration
│   ├── gateway/        # L5: FastAPI, IM gateway, multimodal
│   ├── observability/  # L6: Four-granularity trace, dashboard
│   ├── harness/        # L7: Six governance pillars (core differentiator)
│   └── evolution/      # Agentic RL: feedback, DPO, guardrail
├── apps/               # API, worker, CLI, and mock SaaS applications
├── deploy/             # Docker, Kubernetes, and observability configs
└── docs/               # Documentation
```

## Architecture

Seven-layer architecture with Act -> Observe -> Learn evolution loop,
all governed by platform-level Harness.

See `docs/开发文档/` for detailed design.

## License

MIT
