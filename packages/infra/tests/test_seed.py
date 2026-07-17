"""Deterministic reset and baseline contracts for the demo seed."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from eaos.infra.db import seed


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))


def _migration_created_tables() -> set[str]:
    versions = Path(seed.__file__).resolve().parent / "migrations" / "versions"
    pattern = re.compile(
        r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-z_]+\.[a-z_]+)",
        re.IGNORECASE,
    )
    tables: set[str] = set()
    for path in versions.glob("*.py"):
        for table_name in pattern.findall(path.read_text(encoding="utf-8")):
            normalized = table_name.lower()
            # Truncating the partitioned parent clears spans_p0..spans_p7.
            if normalized.startswith("trace.spans_p"):
                continue
            tables.add(normalized)
    return tables


def test_mutable_table_inventory_matches_all_migrations_through_0018() -> None:
    assert set(seed.MIGRATED_MUTABLE_TABLES) == _migration_created_tables()
    assert not set(seed.PROTECTED_VERSION_TABLES) & set(seed.MIGRATED_MUTABLE_TABLES)


async def test_truncate_covers_all_tables_and_preserves_version_metadata() -> None:
    session = _RecordingSession()

    await seed._truncate(session)  # noqa: SLF001

    assert len(session.statements) == 2
    migrated_sql, checkpoint_sql = session.statements
    positions = [migrated_sql.index(table) for table in seed.MIGRATED_MUTABLE_TABLES]
    assert positions == sorted(positions)
    assert "RESTART IDENTITY CASCADE" in migrated_sql
    for table_name in seed.OPTIONAL_RUNTIME_MUTABLE_TABLES:
        assert f"TRUNCATE TABLE {table_name}" in checkpoint_sql
    for protected in seed.PROTECTED_VERSION_TABLES:
        assert f"TRUNCATE TABLE {protected}" not in "\n".join(session.statements)


def test_expected_seed_baseline_accepts_only_deterministic_counts() -> None:
    counts = dict(seed.EXPECTED_SEED_COUNTS)
    counts.update(dict.fromkeys(seed.OPTIONAL_RUNTIME_MUTABLE_TABLES, 0))

    seed._assert_seed_baseline(counts)  # noqa: SLF001


@pytest.mark.parametrize(
    "table_name",
    [
        "harness.approvals",
        "harness.write_audit",
        "iam.notifications",
        "knowledge.contributions",
        "public.checkpoints",
        "public.checkpoint_blobs",
        "public.checkpoint_writes",
    ],
)
def test_expected_seed_baseline_rejects_residual_mutable_rows(
    table_name: str,
) -> None:
    counts = dict(seed.EXPECTED_SEED_COUNTS)
    counts.update(dict.fromkeys(seed.OPTIONAL_RUNTIME_MUTABLE_TABLES, 0))
    counts[table_name] += 1

    with pytest.raises(RuntimeError, match=re.escape(table_name)):
        seed._assert_seed_baseline(counts)  # noqa: SLF001


@pytest.mark.integration
async def test_live_database_matches_deterministic_seed_baseline() -> None:
    """Run after seeding to verify the committed database baseline."""

    from eaos.core.config import AppConfig
    from eaos.infra.db.postgres import PgClient

    client = PgClient(AppConfig().db)
    try:
        async with client.session() as session:
            await seed._verify_seed_baseline(session)  # noqa: SLF001
    finally:
        await client.close()
