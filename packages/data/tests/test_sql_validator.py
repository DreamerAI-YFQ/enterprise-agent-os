"""Unit tests for SqlValidatorImpl — pure logic, no DB needed."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

from eaos.data.text2sql.validator import SqlValidatorImpl

TID = UUID("00000000-0000-0000-0000-000000000001")
DS_ID = UUID("00000000-0000-0000-0000-000000000509")


def _make_validator() -> SqlValidatorImpl:
    db: Any = MagicMock()
    return SqlValidatorImpl(db)


class TestValidate:
    async def test_valid_select(self) -> None:
        v = _make_validator()
        result = await v.validate("SELECT * FROM erp.products", TID, DS_ID)
        assert result.valid

    async def test_forbidden_insert(self) -> None:
        v = _make_validator()
        result = await v.validate("INSERT INTO erp.products VALUES (1)", TID, DS_ID)
        assert not result.valid
        assert "INSERT" in (result.reason or "")

    async def test_forbidden_delete(self) -> None:
        v = _make_validator()
        result = await v.validate("DELETE FROM erp.products", TID, DS_ID)
        assert not result.valid
        assert "DELETE" in (result.reason or "")

    async def test_forbidden_drop(self) -> None:
        v = _make_validator()
        result = await v.validate("DROP TABLE erp.products", TID, DS_ID)
        assert not result.valid

    async def test_forbidden_update(self) -> None:
        v = _make_validator()
        result = await v.validate("UPDATE erp.products SET name='x'", TID, DS_ID)
        assert not result.valid

    async def test_forbidden_alter(self) -> None:
        v = _make_validator()
        result = await v.validate("ALTER TABLE erp.products DROP COLUMN name", TID, DS_ID)
        assert not result.valid

    async def test_forbidden_truncate(self) -> None:
        v = _make_validator()
        result = await v.validate("TRUNCATE erp.products", TID, DS_ID)
        assert not result.valid

    async def test_case_insensitive_keywords(self) -> None:
        v = _make_validator()
        result = await v.validate("insert into erp.products values (1)", TID, DS_ID)
        assert not result.valid

    async def test_semicolon_rejected(self) -> None:
        v = _make_validator()
        result = await v.validate("SELECT * FROM erp.products;", TID, DS_ID)
        assert not result.valid
        assert "semicolon" in (result.reason or "").lower()

    async def test_comment_rejected(self) -> None:
        v = _make_validator()
        result = await v.validate("SELECT * FROM erp.products -- comment", TID, DS_ID)
        assert not result.valid
        assert "comment" in (result.reason or "").lower()

    async def test_block_comment_rejected(self) -> None:
        v = _make_validator()
        result = await v.validate("SELECT /* hint */ * FROM erp.products", TID, DS_ID)
        assert not result.valid

    async def test_with_select_valid(self) -> None:
        v = _make_validator()
        result = await v.validate("WITH cte AS (SELECT 1) SELECT * FROM cte", TID, DS_ID)
        assert result.valid

    async def test_empty_sql_rejected(self) -> None:
        v = _make_validator()
        result = await v.validate("", TID, DS_ID)
        assert not result.valid

    async def test_select_with_join_valid(self) -> None:
        v = _make_validator()
        sql = (
            "SELECT c.name, o.amount FROM erp.orders o "
            "JOIN erp.customers c ON o.customer_id = c.id"
        )
        result = await v.validate(sql, TID, DS_ID)
        assert result.valid
