.PHONY: sync lint typecheck test migrate seed up down logs serve worker m1 m2 m3 m4 m5 m6 m7

sync:
	uv sync --all-packages

lint:
	uv run ruff check .

typecheck:
	uv run mypy --strict packages/

test:
	uv run pytest

migrate:
	uv run alembic upgrade head

seed:
	uv run python -m eaos.infra.db.seed

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

m1: up
	@echo "Waiting for postgres to become healthy..."
	@timeout /t 10 /nobreak > nul
	docker compose exec -T postgres pg_isready -U eaos -d eaos
	uv run alembic upgrade head
	uv run python -m eaos.infra.db.seed
	@echo ""
	@echo "M1 verification query results:"
	docker compose exec -T postgres psql -U eaos -d eaos -c "SELECT count(*) AS tenants FROM iam.tenants;"
	docker compose exec -T postgres psql -U eaos -d eaos -c "SELECT count(*) AS users FROM iam.users;"
	docker compose exec -T postgres psql -U eaos -d eaos -c "SELECT count(*) AS agents FROM agent.agents;"
	docker compose exec -T postgres psql -U eaos -d eaos -c "SELECT count(*) AS skills FROM skills.skills;"
	@echo ""
	@echo "M1 milestone passed. Jaeger UI: http://localhost:16686"

m2: m1
	set "EAOS_RUN_INTEGRATION=1" && uv run pytest packages/data/tests/test_m2_integration.py packages/knowledge/tests/test_m2_integration.py -m integration -v

m3: m2
	set "EAOS_RUN_INTEGRATION=1" && uv run pytest packages/agent/tests/test_m3_integration.py -m integration -v

m4: m3
	set "EAOS_RUN_INTEGRATION=1" && uv run pytest packages/gateway/tests/test_m4_integration.py -m integration -v

m5: m4
	set "EAOS_RUN_INTEGRATION=1" && uv run pytest packages/evolution/tests/test_m5_integration.py -m integration -v

serve:
	uv run uvicorn eaos_api.main:app --reload --host 0.0.0.0 --port 8000

worker:
	uv run python -m eaos_worker

m6: m5
	set "EAOS_RUN_INTEGRATION=1" && uv run pytest apps/eaos_api/tests/test_m6_integration.py -m integration -v
	@echo ""
	@echo "M6 milestone passed. API: http://localhost:8000/docs"

m7: m6
	set "EAOS_RUN_INTEGRATION=1" && uv run pytest packages/agent/tests/test_m7_integration.py -m integration -v
	@echo ""
	@echo "M7 milestone passed. Tool execution layer verified (mock_saas REST + MCP)."
