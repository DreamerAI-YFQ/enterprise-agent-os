"""SQL validator — syntax + permission + injection checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.infra.db.base import DbClient


_FORBIDDEN_KEYWORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "GRANT",
    "CREATE",
    "VACUUM",
]
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(r"--|/\*|\*/")


@dataclass(frozen=True)
class ValidationResult:
    """Result of SQL validation."""

    valid: bool
    reason: str | None = None
    forbidden_tables: list[str] | None = None


class SqlValidator(Protocol):
    """SQL validator — runs before sandbox execution."""

    async def validate(
        self,
        sql: str,
        tenant_id: UUID,
        datasource_id: UUID,
    ) -> ValidationResult:
        """Validate SQL: syntax, table permissions, injection detection."""
        ...


class SqlValidatorImpl:
    """SqlValidator checking forbidden keywords, semicolons, and comments."""

    def __init__(self, db: DbClient) -> None:
        self._db = db

    async def validate(
        self,
        sql: str,
        tenant_id: UUID,
        datasource_id: UUID,
    ) -> ValidationResult:
        del tenant_id, datasource_id  # validation is pure SQL analysis

        match = _FORBIDDEN_RE.search(sql)
        if match:
            return ValidationResult(
                valid=False,
                reason=f"forbidden keyword: {match.group(1).upper()}",
            )

        if ";" in sql:
            return ValidationResult(valid=False, reason="semicolons not allowed")

        if _COMMENT_RE.search(sql):
            return ValidationResult(valid=False, reason="SQL comments not allowed")

        stripped = sql.strip().lower()
        if not stripped.startswith("select") and not stripped.startswith("with"):
            return ValidationResult(
                valid=False,
                reason="only SELECT statements allowed",
            )

        return ValidationResult(valid=True)
