"""Verify data layer Protocols match Phase 0 contract."""

from __future__ import annotations

import dataclasses

from eaos.data.connector import (
    DataConnector,
    DataResource,
    DataResult,
    ReadQuery,
    SchemaDescription,
    WriteOperation,
    WriteResult,
)
from eaos.data.mcp.server import EnterpriseMCPServer
from eaos.data.text2sql.engine import QueryResult, Text2SQLEngine
from eaos.data.text2sql.sandbox import SqlSandbox
from eaos.data.text2sql.validator import SqlValidator


class TestDataConnector:
    def test_protocol_methods(self) -> None:
        for method in (
            "list_resources",
            "read",
            "write",
            "describe_schema",
            "rollback",
        ):
            assert hasattr(DataConnector, method)

    def test_dataclasses(self) -> None:
        for cls in (
            DataResource,
            ReadQuery,
            WriteOperation,
            DataResult,
            WriteResult,
            SchemaDescription,
        ):
            assert dataclasses.is_dataclass(cls)


class TestText2SQL:
    def test_queryresult_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(QueryResult)}
        assert {"rows", "sql", "explanation", "truncated", "error"} <= fields

    def test_engine_protocol(self) -> None:
        assert hasattr(Text2SQLEngine, "query")


class TestMCPServer:
    def test_protocol_methods(self) -> None:
        for method in (
            "list_tools",
            "call_tool",
            "list_resources",
            "read_resource",
        ):
            assert hasattr(EnterpriseMCPServer, method)


class TestSqlValidatorAndSandbox:
    def test_validator_protocol(self) -> None:
        assert hasattr(SqlValidator, "validate")

    def test_sandbox_protocol(self) -> None:
        assert hasattr(SqlSandbox, "execute")
