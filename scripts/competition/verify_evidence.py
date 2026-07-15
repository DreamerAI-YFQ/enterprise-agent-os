"""Evidence verification — SHA-256 integrity check for competition artifacts.

Verifies that all evidence files in a run directory match their manifest
hashes, ensuring no tampering after export.

Usage:
    python scripts/competition/verify_evidence.py --run-id <run_id>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_run(run_id: str, base_dir: Path | None = None) -> int:
    """Verify all artifacts in a run directory against the manifest."""
    if base_dir is None:
        base_dir = Path("artifacts/competition-evidence")

    run_dir = base_dir / run_id
    manifest_file = run_dir / "manifest.json"

    if not manifest_file.exists():
        print(f"ERROR: Manifest not found: {manifest_file}")
        return 1

    with open(manifest_file, encoding="utf-8") as f:
        manifest = json.load(f)

    print(f"Verifying run '{run_id}'...")
    print(f"  Timestamp: {manifest.get('timestamp', 'unknown')}")
    print(f"  Git SHA: {manifest.get('git_sha', 'unknown')}")
    print(f"  Artifacts: {len(manifest.get('artifacts', []))}")
    print()

    all_ok = True
    for artifact in manifest.get("artifacts", []):
        filepath = Path(artifact["file"])
        expected_hash = artifact["sha256"]

        if not filepath.exists():
            print(f"  [MISSING] {filepath}")
            all_ok = False
            continue

        actual_hash = _sha256_file(filepath)
        if actual_hash == expected_hash:
            rows = artifact.get("rows", artifact.get("total_tokens", "?"))
            print(f"  [OK]      {filepath.name} ({rows} rows)")
        else:
            print(f"  [FAIL]    {filepath.name}")
            print(f"            expected: {expected_hash}")
            print(f"            actual:   {actual_hash}")
            all_ok = False

    print()
    if all_ok:
        print(f"PASS: All {len(manifest.get('artifacts', []))} artifacts verified.")
        return 0
    else:
        print("FAIL: Some artifacts failed verification.")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify competition evidence")
    parser.add_argument("--run-id", required=True, help="Run identifier to verify")
    parser.add_argument("--base-dir", default=None, help="Base evidence directory")
    args = parser.parse_args()

    base_dir = Path(args.base_dir) if args.base_dir else None
    return verify_run(args.run_id, base_dir)


if __name__ == "__main__":
    sys.exit(main())
