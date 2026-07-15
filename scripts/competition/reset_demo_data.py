"""C00-03: Competition fixture reset script.

Resets the demo environment to a known-good state for competition evaluation.
This ensures reproducibility: every run starts from the same baseline.

Operations:
1. Reset ERP tables (orders, inventory) to seed state
2. Clear agent sessions and messages
3. Clear approval records (keep schema)
4. Clear audit trail (keep schema)
5. Clear trace records
6. Clear knowledge contributions (keep published documents)
7. Reset checkpoints (LangGraph state)

Usage:
    python scripts/competition/reset_demo_data.py [--dry-run]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Any


def _exec_sql(sql: str, dry_run: bool = False) -> Any:
    """Execute SQL in the eaos-postgres container."""
    if dry_run:
        print(f"  [DRY-RUN] SQL: {sql[:80]}...")
        return None
    result = subprocess.run(
        ["docker", "exec", "eaos-postgres",
         "psql", "-U", "eaos", "-d", "eaos", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"  [ERROR] SQL failed: {result.stderr.strip()}")
    return result


def reset_erp(dry_run: bool = False) -> None:
    """Reset ERP tables to seed state."""
    print("Resetting ERP tables...")
    # Delete all orders (they're test-generated)
    _exec_sql("DELETE FROM erp.orders WHERE order_no LIKE 'ORD-2024-%'", dry_run)
    # Reset inventory to seed quantities
    _exec_sql(
        "UPDATE erp.inventory SET quantity = safety_stock * 2 "
        "WHERE warehouse = 'WH-01'",
        dry_run,
    )
    print("  OK" if not dry_run else "  [DRY-RUN]")


def reset_sessions(dry_run: bool = False) -> None:
    """Clear agent sessions and messages."""
    print("Clearing agent sessions and messages...")
    _exec_sql("DELETE FROM agent.messages", dry_run)
    _exec_sql("DELETE FROM agent.sessions", dry_run)
    print("  OK" if not dry_run else "  [DRY-RUN]")


def reset_governance(dry_run: bool = False) -> None:
    """Clear approvals and audit trail."""
    print("Clearing governance records...")
    _exec_sql("DELETE FROM harness.write_audit", dry_run)
    _exec_sql("DELETE FROM harness.approvals", dry_run)
    print("  OK" if not dry_run else "  [DRY-RUN]")


def reset_traces(dry_run: bool = False) -> None:
    """Clear trace records."""
    print("Clearing traces...")
    _exec_sql("DELETE FROM observability.traces", dry_run)
    print("  OK" if not dry_run else "  [DRY-RUN]")


def reset_contributions(dry_run: bool = False) -> None:
    """Clear pending knowledge contributions (keep approved)."""
    print("Clearing pending contributions...")
    _exec_sql("DELETE FROM knowledge.contributions WHERE status = 'pending'", dry_run)
    print("  OK" if not dry_run else "  [DRY-RUN]")


def reset_checkpoints(dry_run: bool = False) -> None:
    """Clear LangGraph checkpoint state."""
    print("Clearing LangGraph checkpoints...")
    _exec_sql("DELETE FROM checkpoints", dry_run)
    _exec_sql("DELETE FROM checkpoint_writes", dry_run)
    _exec_sql("DELETE FROM checkpoint_blobs", dry_run)
    print("  OK" if not dry_run else "  [DRY-RUN]")


def verify_seed(dry_run: bool = False) -> None:
    """Verify seed data is intact."""
    print("Verifying seed data...")
    tables = {
        "iam.tenants": 1,
        "iam.users": 3,
        "erp.products": 10,
        "erp.customers": 5,
    }
    all_ok = True
    for table, expected_min in tables.items():
        result = subprocess.run(
            ["docker", "exec", "eaos-postgres",
             "psql", "-U", "eaos", "-d", "eaos", "-t", "-A", "-c",
             f"SELECT COUNT(*) FROM {table}"],
            capture_output=True, text=True, timeout=10,
        )
        try:
            count = int(result.stdout.strip())
            status = "OK" if count >= expected_min else "FAIL"
            if status == "FAIL":
                all_ok = False
            print(f"  [{status}] {table}: {count} rows (expected >= {expected_min})")
        except (ValueError, IndexError):
            print(f"  [ERROR] {table}: could not count")
            all_ok = False

    if not all_ok:
        print("\nWARNING: Seed data verification failed. Run seed first:")
        print("  docker exec eaos-api uv run python -m eaos.infra.db.seed")
        return 1
    return 0


def main(dry_run: bool = False) -> int:
    print("=" * 60)
    print("Competition Demo Data Reset")
    print("=" * 60)
    print()

    if dry_run:
        print("** DRY RUN MODE — no changes will be made **\n")

    reset_erp(dry_run)
    reset_sessions(dry_run)
    reset_governance(dry_run)
    reset_traces(dry_run)
    reset_contributions(dry_run)
    reset_checkpoints(dry_run)

    print()
    if not dry_run:
        rc = verify_seed(dry_run)
        print()
        if rc == 0:
            print("Reset complete. Environment is ready for competition tests.")
        else:
            print("Reset complete with warnings. Check seed data.")
        return rc
    else:
        print("Dry run complete. Run without --dry-run to apply.")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset demo data for competition")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()
    sys.exit(main(dry_run=args.dry_run))
