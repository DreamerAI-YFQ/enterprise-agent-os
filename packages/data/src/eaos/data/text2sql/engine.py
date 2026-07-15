"""Text2SQL engine protocol — natural language to SQL query.

Schema-aware, multi-model, with self-correction. Implementations live in
Phase 2; Phase 0 only defines the interface.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.core.context import TenantContext
    from eaos.data.connector import DataConnector, SchemaDescription
    from eaos.data.text2sql.sandbox import SqlSandbox
    from eaos.data.text2sql.validator import SqlValidator, ValidationResult
    from eaos.infra.db.base import DbClient
    from eaos.infra.llm.base import LLMResponse
    from eaos.infra.llm.router import LLMRouter
    from eaos.knowledge.ontology.repository import OntologyRepository


@dataclass(frozen=True)
class QueryResult:
    """Result of a Text2SQL query."""

    rows: list[dict[str, Any]]
    sql: str
    explanation: str | None
    truncated: bool = False
    error: str | None = None


class Text2SQLEngine(Protocol):
    """Natural language to SQL engine."""

    async def query(
        self,
        natural_query: str,
        ctx: TenantContext,
        datasource_id: UUID,
    ) -> QueryResult:
        """Translate natural_query to SQL, execute on datasource, return result."""
        ...


class Text2SQLEngineImpl:
    """Text2SQLEngine with schema-aware LLM generation and self-correction.

    Flow: fetch datasource -> resolve connector -> describe all resource
    schemas -> fetch ontology Chinese-name mapping -> LLM generates SQL ->
    validate -> sandbox execute -> self-correct on validation failure
    (up to MAX_RETRIES) -> record history -> return QueryResult.

    Sandbox execution errors are swallowed (PgSqlSandbox returns []), so
    self-correction triggers on *validation* failure only — the primary
    signal for bad LLM output (forbidden keywords, non-SELECT, comments).
    """

    MAX_RETRIES = 3

    SYSTEM_PROMPT = (
        "你是 SQL 专家。根据表结构生成 PostgreSQL 查询。\n"
        "规则:\n"
        "1. 只生成 SELECT 语句\n"
        "2. 使用标准 PostgreSQL 语法\n"
        "3. 表名需带 schema 前缀（如 erp.customers）\n"
        '4. 返回 JSON: {"sql": "...", "explanation": "..."}'
    )

    def __init__(
        self,
        llm: LLMRouter,
        ontology_repo: OntologyRepository,
        connectors: dict[str, DataConnector],
        validator: SqlValidator,
        sandbox: SqlSandbox,
        db: DbClient,
    ) -> None:
        self._llm = llm
        self._ontology_repo = ontology_repo
        self._connectors = connectors
        self._validator = validator
        self._sandbox = sandbox
        self._db = db

    async def query(
        self,
        natural_query: str,
        ctx: TenantContext,
        datasource_id: UUID,
    ) -> QueryResult:
        start = time.monotonic()

        connector_name, ds_error = await self._resolve_connector(
            ctx.tenant_id, datasource_id
        )
        if ds_error is not None:
            await self._record_history(
                ctx, datasource_id, natural_query, "", False, False, 0,
                ds_error, 0,
            )
            return QueryResult(rows=[], sql="", explanation=None, error=ds_error)

        connector = self._connectors.get(connector_name)
        if connector is None:
            error = f"unknown connector: {connector_name}"
            await self._record_history(
                ctx, datasource_id, natural_query, "", False, False, 0,
                error, 0,
            )
            return QueryResult(rows=[], sql="", explanation=None, error=error)

        schema_text, mapping_text = await self._build_schema_context(
            connector, ctx, datasource_id
        )

        sql, explanation, validation = await self._generate_and_validate(
            natural_query, schema_text, mapping_text, ctx, datasource_id
        )

        if sql == "" or validation is None or not validation.valid:
            if validation is not None and not validation.valid:
                fail_reason: str = validation.reason or "validation failed"
            else:
                fail_reason = "SQL generation failed after retries"
            latency = int((time.monotonic() - start) * 1000)
            await self._record_history(
                ctx, datasource_id, natural_query, sql, False, False, 0,
                fail_reason, latency,
            )
            return QueryResult(
                rows=[], sql=sql, explanation=explanation, error=fail_reason
            )

        rows = await self._sandbox.execute(sql, ctx.tenant_id, datasource_id)
        latency = int((time.monotonic() - start) * 1000)
        await self._record_history(
            ctx, datasource_id, natural_query, sql, True, True, len(rows),
            None, latency,
        )
        return QueryResult(
            rows=rows,
            sql=sql,
            explanation=explanation,
            truncated=False,  # TODO(Phase 3): sandbox should signal truncation
        )

    async def _resolve_connector(
        self, tenant_id: UUID, datasource_id: UUID
    ) -> tuple[str, str | None]:
        """Fetch datasource row; return (connector_name, error)."""
        rows = await self._db.tenant_scoped_fetch(
            "SELECT connection FROM data.datasources "
            "WHERE id = :p0 AND tenant_id = :tenant_id",
            tenant_id,
            datasource_id,
        )
        if not rows:
            return "", f"datasource {datasource_id} not found"
        conn_raw = rows[0].get("connection")
        conn: dict[str, Any] = (
            json.loads(conn_raw) if isinstance(conn_raw, str) else (conn_raw or {})
        )
        connector_name = conn.get("connector")
        if not connector_name:
            return "", "datasource has no 'connector' in connection config"
        return str(connector_name), None

    async def _build_schema_context(
        self,
        connector: DataConnector,
        ctx: TenantContext,
        datasource_id: UUID,
    ) -> tuple[str, str]:
        """Describe all resources + fetch ontology mapping; return (schema_text, mapping_text)."""
        resources = await connector.list_resources(ctx.tenant_id)
        schemas: list[SchemaDescription] = []
        for res in resources:
            schema = await connector.describe_schema(ctx.tenant_id, res.name)
            schemas.append(schema)
        schema_text = self._format_schemas(schemas)
        mapping = await self._ontology_repo.get_schema_mapping(
            ctx.tenant_id, datasource_id
        )
        mapping_text = json.dumps(mapping, ensure_ascii=False, indent=2)
        return schema_text, mapping_text

    @staticmethod
    def _format_schemas(schemas: list[SchemaDescription]) -> str:
        """Format schema descriptions into a readable text block for the LLM."""
        parts: list[str] = []
        for i, s in enumerate(schemas, 1):
            lines = [f"{i}. {s.table_name}"]
            for col in s.columns:
                nullable = "NULL" if col.get("nullable") else "NOT NULL"
                comment = col.get("comment") or ""
                lines.append(
                    f"   - {col['name']} ({col['type']}, {nullable}) {comment}".rstrip()
                )
            if s.sample_rows:
                lines.append(f"   示例数据 ({len(s.sample_rows)} rows):")
                for row in s.sample_rows:
                    lines.append(
                        f"     {json.dumps(row, ensure_ascii=False, default=str)}"
                    )
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    async def _generate_and_validate(
        self,
        natural_query: str,
        schema_text: str,
        mapping_text: str,
        ctx: TenantContext,
        datasource_id: UUID,
    ) -> tuple[str, str | None, ValidationResult | None]:
        """Generate SQL via LLM and validate; self-correct on failure up to MAX_RETRIES."""
        error_feedback: str | None = None
        sql = ""
        explanation: str | None = None
        validation: ValidationResult | None = None
        for _attempt in range(self.MAX_RETRIES + 1):
            sql, explanation = await self._generate_sql(
                natural_query, schema_text, mapping_text, error_feedback
            )
            if sql == "":
                error_feedback = "LLM did not return valid SQL JSON"
                continue
            validation = await self._validator.validate(
                sql, ctx.tenant_id, datasource_id
            )
            if validation.valid:
                break
            error_feedback = f"Validation failed: {validation.reason}"
        return sql, explanation, validation

    async def _generate_sql(
        self,
        natural_query: str,
        schema_text: str,
        mapping_text: str,
        error_feedback: str | None,
    ) -> tuple[str, str | None]:
        """Call LLM to generate SQL; return (sql, explanation). ("", None) on parse failure."""
        from eaos.infra.llm.base import Message

        user_content = (
            f"表结构:\n{schema_text}\n\n"
            f"字段中文释义:\n{mapping_text}\n\n"
            f"用户查询: {natural_query}"
        )
        if error_feedback:
            user_content += f"\n\n上次错误: {error_feedback}\n请修正。"
        messages = [
            Message(role="system", content=self.SYSTEM_PROMPT),
            Message(role="user", content=user_content),
        ]
        response = await self._llm.chat(
            messages, task_type="text2sql", temperature=0.0
        )
        return self._parse_sql_response(response)

    @staticmethod
    def _parse_sql_response(response: LLMResponse) -> tuple[str, str | None]:
        """Parse LLM JSON {sql, explanation}; return ("", None) on failure."""
        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, TypeError):
            return "", None
        if not isinstance(data, dict):
            return "", None
        sql = data.get("sql", "")
        if not isinstance(sql, str) or sql.strip() == "":
            return "", None
        explanation = data.get("explanation")
        if not isinstance(explanation, str):
            explanation = None
        return sql.strip(), explanation

    async def _record_history(
        self,
        ctx: TenantContext,
        datasource_id: UUID,
        natural_query: str,
        sql: str,
        executed: bool,
        success: bool,
        result_count: int,
        error: str | None,
        latency_ms: int,
    ) -> None:
        """INSERT INTO data.query_history (best-effort; swallows errors)."""
        with contextlib.suppress(Exception):
            await self._db.execute(
                "INSERT INTO data.query_history "
                "(tenant_id, datasource_id, user_id, natural_query, "
                "generated_sql, executed, success, result_count, "
                "error_message, latency_ms) "
                "VALUES (:p0, :p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9)",
                ctx.tenant_id,
                datasource_id,
                ctx.user_id,
                natural_query,
                sql,
                executed,
                success,
                result_count,
                error,
                latency_ms,
            )
