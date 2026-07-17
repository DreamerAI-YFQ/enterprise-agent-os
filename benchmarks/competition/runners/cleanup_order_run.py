"""Clean retained database state for one order evaluation result file.

This command is needed only when the main evaluator retained state because
evidence export failed.  It scopes deletion to session UUIDs present in the
specified ``order_results.jsonl`` and prints a verification receipt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

COMPETITION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPETITION_ROOT))

from runners.order_state_machine import (  # noqa: E402
    cleanup_order_run_state,
    collect_created_order_ids,
)


def load_session_ids(path: Path) -> list[str]:
    """Load unique session ids from a state-machine JSONL artifact."""

    session_ids: list[str] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            result: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
        if not isinstance(result, dict):
            raise ValueError(f"line {line_number} is not a JSON object")
        candidates = list(result.get("session_ids") or [])
        if result.get("session_id"):
            candidates.append(result["session_id"])
        for value in candidates:
            text = str(value)
            if text not in session_ids:
                session_ids.append(text)
    return session_ids


async def main(results: Path, tenant_slug: str) -> int:
    if not results.is_file():
        print(f"Order results file not found: {results}")
        return 2
    try:
        session_ids = load_session_ids(results)
        result_rows = [
            json.loads(line)
            for line in results.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        record_ids = collect_created_order_ids(result_rows)
        manifest_path = results.with_name("order_run_manifest.json")
        approver_user_id = None
        fixture_tenant_id = None
        manifest_run_id = None
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest, dict):
                manifest_run_id = str(manifest.get("run_id") or "") or None
                approver_user_id = str(
                    (manifest.get("independent_approver") or {}).get("user_id") or ""
                ) or None
                fixture_tenant_id = str(
                    (manifest.get("cross_tenant_fixture") or {}).get("tenant_id") or ""
                ) or None
        receipt = await cleanup_order_run_state(
            tenant_slug,
            session_ids,
            approver_user_id,
            record_ids,
            fixture_tenant_id,
            manifest_run_id,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must provide a concise receipt
        print(json.dumps({"succeeded": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps(receipt, indent=2, ensure_ascii=False, default=str))
    return 0 if receipt.get("succeeded") is True else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean one retained order evaluation run")
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--tenant-slug", default="acme-corp")
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(arguments.results, arguments.tenant_slug)))
