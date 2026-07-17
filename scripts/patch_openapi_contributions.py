"""Add /knowledge/contributions* and /admin/contributions* paths to frontend/openapi.json.

Idempotent: skips paths that already exist. Run from project root:
    uv run python scripts/patch_openapi_contributions.py
Then regenerate shared types:
    pnpm --filter @eaos/shared gen
"""

from __future__ import annotations

import json
from pathlib import Path

OPENAPI_PATH = Path("frontend/openapi.json")


def _contribution_out_schema() -> dict:
    """Response schema — matches _contrib_to_dict in contributions.py."""
    return {
        "type": "object",
        "title": "ContributionOut",
        "properties": {
            "id": {"type": "string", "format": "uuid"},
            "tenant_id": {"type": "string", "format": "uuid"},
            "submitter_id": {"type": "string", "format": "uuid"},
            "source_type": {"type": "string"},
            "source_uri": {"type": "string", "nullable": True},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "status": {"type": "string"},
            "reviewer_id": {"type": "string", "format": "uuid", "nullable": True},
            "review_comment": {"type": "string", "nullable": True},
            "submitted_at": {"type": "string", "nullable": True},
            "reviewed_at": {"type": "string", "nullable": True},
            "metadata": {"type": "object", "additionalProperties": True},
        },
        "required": [
            "id",
            "tenant_id",
            "submitter_id",
            "source_type",
            "title",
            "content",
            "status",
        ],
    }


def _contribution_create_schema() -> dict:
    return {
        "properties": {
            "title": {"type": "string", "title": "Title"},
            "content": {"type": "string", "title": "Content"},
            "source_type": {"type": "string", "title": "Source Type", "default": "manual"},
            "source_uri": {"type": "string", "title": "Source Uri", "nullable": True},
            "metadata": {
                "additionalProperties": True,
                "type": "object",
                "title": "Metadata",
                "default": {},
            },
        },
        "type": "object",
        "required": ["title", "content"],
        "title": "ContributionCreate",
        "description": "Request body for POST /knowledge/contributions — submit a contribution.",
    }


def _contribution_review_schema() -> dict:
    return {
        "properties": {
            "decision": {"type": "string", "enum": ["approved", "rejected"], "title": "Decision"},
            "reason": {"type": "string", "title": "Reason", "nullable": True},
        },
        "type": "object",
        "required": ["decision"],
        "title": "ContributionReview",
        "description": "Request body for POST /admin/contributions/{id}/review.",
    }


def _validation_error_response() -> dict:
    return {
        "description": "Validation Error",
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}}
        },
    }


def _json_response(schema_ref: str = "#/components/schemas/ContributionOut") -> dict:
    return {
        "description": "Successful Response",
        "content": {"application/json": {"schema": {"$ref": schema_ref}}},
    }


def _list_response(item_ref: str = "#/components/schemas/ContributionOut") -> dict:
    return {
        "description": "Successful Response",
        "content": {
            "application/json": {
                "schema": {
                    "type": "array",
                    "items": {"$ref": item_ref},
                }
            }
        },
    }


def _query_params() -> list[dict]:
    return [
        {
            "name": "limit",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
        },
        {
            "name": "offset",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 0, "minimum": 0},
        },
    ]


def build_paths() -> dict:
    return {
        "/knowledge/contributions": {
            "post": {
                "tags": ["contributions"],
                "summary": "Submit Contribution",
                "description": (
                    "Submit a knowledge contribution for admin review (employee + admin)."
                ),
                "operationId": "submit_contribution_knowledge_contributions_post",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ContributionCreate"}
                        }
                    },
                },
                "responses": {
                    "201": _json_response(),
                    "422": _validation_error_response(),
                },
            }
        },
        "/knowledge/contributions/mine": {
            "get": {
                "tags": ["contributions"],
                "summary": "List My Contributions",
                "description": "List the current user's own contributions.",
                "operationId": "list_my_contributions_knowledge_contributions_mine_get",
                "parameters": _query_params(),
                "responses": {
                    "200": _list_response(),
                    "422": _validation_error_response(),
                },
            }
        },
        "/admin/contributions": {
            "get": {
                "tags": ["contributions"],
                "summary": "List Contributions",
                "description": "List all contributions for the tenant (admin only).",
                "operationId": "list_contributions_admin_contributions_get",
                "parameters": [
                    {
                        "name": "status",
                        "in": "query",
                        "required": False,
                        "schema": {
                            "type": "string",
                            "pattern": "^(pending|approved|rejected)$",
                        },
                    },
                    *_query_params(),
                ],
                "responses": {
                    "200": _list_response(),
                    "422": _validation_error_response(),
                },
            }
        },
        "/admin/contributions/{contribution_id}": {
            "get": {
                "tags": ["contributions"],
                "summary": "Get Contribution",
                "description": "Get a single contribution (admin only).",
                "operationId": "get_contribution_admin_contributions_{contribution_id}_get",
                "parameters": [
                    {
                        "name": "contribution_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": _json_response(),
                    "404": {
                        "description": "Not Found",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                },
            }
        },
        "/admin/contributions/{contribution_id}/review": {
            "post": {
                "tags": ["contributions"],
                "summary": "Review Contribution",
                "description": "Approve or reject a contribution (admin only).",
                "operationId": (
                    "review_contribution_admin_contributions_{contribution_id}_review_post"
                ),
                "parameters": [
                    {
                        "name": "contribution_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ContributionReview"}
                        }
                    },
                },
                "responses": {
                    "200": _json_response(),
                    "404": {
                        "description": "Not Found",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "409": {
                        "description": "Conflict",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "422": _validation_error_response(),
                },
            }
        },
    }


def main() -> None:
    spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    paths = spec.setdefault("paths", {})
    schemas = spec.setdefault("components", {}).setdefault("schemas", {})

    new_paths = build_paths()
    added_paths = 0
    for path, methods in new_paths.items():
        if path in paths:
            print(f"  skip path (exists): {path}")
            continue
        paths[path] = methods
        added_paths += 1
        print(f"  added path: {path}")

    new_schemas = {
        "ContributionCreate": _contribution_create_schema(),
        "ContributionReview": _contribution_review_schema(),
        "ContributionOut": _contribution_out_schema(),
    }
    added_schemas = 0
    for name, schema in new_schemas.items():
        if name in schemas:
            print(f"  skip schema (exists): {name}")
            continue
        schemas[name] = schema
        added_schemas += 1
        print(f"  added schema: {name}")

    OPENAPI_PATH.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nDone: +{added_paths} paths, +{added_schemas} schemas")


if __name__ == "__main__":
    main()
