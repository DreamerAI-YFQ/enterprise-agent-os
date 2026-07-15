"""Export the FastAPI app's OpenAPI schema to frontend/openapi.json.

Run: uv run python scripts/export_openapi.py
"""
import json
import sys
from pathlib import Path


def main() -> int:
    try:
        from eaos_api.main import app
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR importing app: {exc}", file=sys.stderr)
        return 1

    schema = app.openapi()
    out = Path("frontend/openapi.json")
    out.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    n_paths = len(schema.get("paths", {}))
    print(f"exported {n_paths} paths -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
