"""Spec-driven configuration for HTTP API connectors.

These dataclasses define the static configuration for an ``HttpApiConnector``
— endpoint mappings, auth scheme, pagination strategy. The spec is
serializable and can be stored in the ``data.external_connections`` table
(T5). Credentials are NOT included in the spec; they are injected at
runtime by ``ConnectionManager`` from encrypted storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PaginationSpec:
    """Pagination strategy for list endpoints.

    - ``cursor``: response contains a ``next_cursor`` field; pass it as a
      query param named ``cursor_param``.
    - ``offset``: use ``offset_param`` and ``limit_param`` query params.
    - ``page``: use ``page_param`` and ``page_size_param`` query params.
    """

    type: str  # "cursor" | "offset" | "page"
    cursor_param: str = "cursor"
    cursor_response_field: str = "next_cursor"
    offset_param: str = "offset"
    limit_param: str = "limit"
    page_param: str = "page"
    page_size_param: str = "page_size"
    data_field: str = "data"
    total_field: str = "total"


@dataclass(frozen=True)
class ResourceSpec:
    """Endpoint mapping for a single resource type.

    ``path`` may contain ``{id}`` placeholder for record-level operations.
    ``methods`` lists the HTTP methods supported by this resource.
    """

    path: str  # "/api/v1/orders/{id}"
    methods: list[str]  # ["GET", "POST", "PUT", "DELETE"]
    id_field: str  # "id"
    schema: dict[str, Any] = field(default_factory=dict)
    access_mode: str = "read_write"  # "read" | "read_write"


@dataclass(frozen=True)
class HttpApiSpec:
    """Full specification for an HTTP API connector.

    The spec describes how to interact with a SaaS REST API: base URL,
    resource endpoint mappings, auth scheme, pagination, and health check.
    All static configuration lives here; credentials are injected separately.
    """

    base_url: str
    resources: dict[str, ResourceSpec]  # resource_name → endpoint mapping
    health_check_path: str | None = None
    default_timeout: float = 30.0
    pagination: PaginationSpec | None = None


@dataclass(frozen=True)
class HttpAuth:
    """Authentication scheme configuration.

    Credentials (token, API key, username/password) are NOT stored here.
    At runtime, ``ConnectionManager`` (T5) resolves the auth and injects
    the appropriate headers/params.

    - ``oauth2``: ``token_endpoint`` is used to obtain/refresh access tokens.
    - ``api_key``: ``header_name`` is the HTTP header for the key.
    - ``basic``: standard HTTP Basic auth.
    """

    type: str  # "oauth2" | "api_key" | "basic"
    token_endpoint: str | None = None
    header_name: str = "Authorization"
    header_prefix: str = "Bearer"  # "Bearer" for OAuth2, "" for API Key
