"""Generic HTTP API connector — bridges SaaS systems without MCP server.

Implements the ``DataConnector`` Protocol for REST APIs. Behavior is fully
driven by ``HttpApiSpec`` (endpoint mappings, pagination) and ``HttpAuth``
(auth scheme). Credentials are injected at construction time — the spec
contains no secrets and can be stored in version control.

Write operations capture a ``before`` snapshot for rollback: ``update`` and
``delete`` fetch the current state first, ``create`` does not (nothing to
snapshot). ``rollback()`` reverses the operation using the snapshot.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from eaos.data.connector import (
    DataResource,
    DataResult,
    ReadQuery,
    SchemaDescription,
    WriteOperation,
    WriteResult,
)

if TYPE_CHECKING:
    from uuid import UUID

    from eaos.data.http_spec import HttpApiSpec, HttpAuth, PaginationSpec

logger = logging.getLogger(__name__)


class HttpApiConnector:
    """Spec-driven REST API connector implementing ``DataConnector``.

    Use ``register`` in ``ConnectionManager`` (T5) to create instances.
    For tests, pass a mock ``httpx.AsyncClient`` and verify request URLs,
    methods, and payloads.
    """

    def __init__(
        self,
        spec: HttpApiSpec,
        auth: HttpAuth,
        http_client: httpx.AsyncClient,
        credentials: dict[str, str],
    ) -> None:
        self._spec = spec
        self._auth = auth
        self._client = http_client
        self._credentials = credentials
        self._access_token: str | None = credentials.get("access_token")
        self._token_expires_at: float | None = None

    # -- DataConnector: list_resources -----------------------------------

    async def list_resources(self, tenant_id: UUID) -> list[DataResource]:
        """Return resources from the spec (no HTTP call needed)."""
        del tenant_id
        resources: list[DataResource] = []
        for name, res_spec in self._spec.resources.items():
            resources.append(
                DataResource(
                    name=name,
                    display_name=name.replace("_", " ").title(),
                    description=res_spec.schema.get("description", ""),
                    access_mode=res_spec.access_mode,
                )
            )
        return resources

    # -- DataConnector: read ---------------------------------------------

    async def read(
        self,
        tenant_id: UUID,
        resource: str,
        query: ReadQuery,
    ) -> DataResult:
        """GET {base_url}/{resource_path} with query parameters."""
        del tenant_id

        res_spec = self._spec.resources.get(resource)
        if res_spec is None:
            return DataResult(rows=[], total=0)

        # A resource path with ``{id}`` exposes a record endpoint.  Honour an
        # exact id-field filter through that endpoint instead of forwarding it
        # to a collection API which may silently ignore unsupported filters.
        # Rollback verification relies on reading the intended record, not the
        # first row of a paginated collection.
        record_id = query.filters.get(res_spec.id_field)
        if record_id is not None and "{id}" in res_spec.path:
            item = await self._fetch_single(resource, str(record_id))
            if item is None:
                return DataResult(rows=[], total=0)
            for key, expected in query.filters.items():
                if key == res_spec.id_field:
                    continue
                if str(item.get(key, "")) != str(expected):
                    return DataResult(rows=[], total=0)
            if query.fields:
                item = {key: item.get(key) for key in query.fields}
            return DataResult(rows=[item], total=1)

        path = res_spec.path.replace("/{id}", "")
        params = self._build_query_params(query)
        url = f"{self._spec.base_url}{path}"

        try:
            resp = await self._request("GET", url, params=params)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "read failed: %s %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            return DataResult(rows=[], total=0)

        body = resp.json()
        pagination = self._spec.pagination
        rows, total = self._extract_rows(body, pagination)
        return DataResult(rows=rows, total=total)

    # -- DataConnector: write --------------------------------------------

    async def write(
        self,
        tenant_id: UUID,
        resource: str,
        operation: WriteOperation,
    ) -> WriteResult:
        """Execute a write operation (create/update/delete) via HTTP."""
        del tenant_id

        res_spec = self._spec.resources.get(resource)
        if res_spec is None:
            return WriteResult(success=False, error=f"unknown resource: {resource}")

        if res_spec.access_mode == "read":
            return WriteResult(
                success=False, error=f"resource {resource} is read-only"
            )

        before: dict[str, Any] | None = None
        if operation.operation in ("update", "delete") and operation.record_id:
            before = await self._fetch_single(resource, operation.record_id)
            if before is None:
                return WriteResult(
                    success=False,
                    error=f"record not found: {operation.record_id}",
                )

        try:
            if operation.operation == "create":
                after = await self._do_create(resource, operation.data)
            elif operation.operation == "update":
                after = await self._do_update(
                    resource, operation.record_id or "", operation.data
                )
            elif operation.operation == "delete":
                await self._do_delete(resource, operation.record_id or "")
                after = None
            else:
                return WriteResult(
                    success=False, error=f"unknown operation: {operation.operation}"
                )
        except httpx.HTTPStatusError as exc:
            return WriteResult(
                success=False,
                error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
                before=before,
            )
        except Exception as exc:
            return WriteResult(success=False, error=str(exc), before=before)

        return WriteResult(success=True, before=before, after=after)

    # -- DataConnector: describe_schema ----------------------------------

    async def describe_schema(
        self,
        tenant_id: UUID,
        resource: str,
    ) -> SchemaDescription:
        del tenant_id
        res_spec = self._spec.resources.get(resource)
        if res_spec is None:
            raise ValueError(f"unknown resource: {resource}")
        columns = res_spec.schema.get("columns", [])
        return SchemaDescription(
            table_name=resource,
            columns=list(columns) if isinstance(columns, list) else [],
            relations=[],
            sample_rows=[],
        )

    # -- DataConnector: rollback -----------------------------------------

    async def rollback(self, tenant_id: UUID, snapshot: dict[str, Any]) -> None:
        """Reverse a write operation using the before snapshot.

        ``snapshot`` must contain: ``operation``, ``resource``, ``record_id``,
        and ``before`` (the state before the write, or None for create).
        """
        del tenant_id

        op = snapshot.get("operation", "")
        resource = snapshot.get("resource", "")
        record_id = snapshot.get("record_id", "")
        before = snapshot.get("before")

        try:
            if op == "create" and record_id:
                await self._do_delete(resource, record_id)
            elif op == "update" and before and record_id:
                await self._do_update(resource, record_id, before)
            elif op == "delete" and before:
                await self._do_create(resource, before)
        except Exception:
            logger.exception("rollback failed for %s %s", op, resource)

    # -- DataConnector: health_check (extension) -------------------------

    async def health_check(self) -> bool:
        """GET {base_url}/{health_check_path} → 200 = healthy."""
        path = self._spec.health_check_path or "/health"
        url = f"{self._spec.base_url}{path}"
        try:
            resp = await self._request("GET", url)
            return resp.status_code < 400
        except Exception:
            return False

    # -- HTTP helpers ----------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Send an authenticated HTTP request."""
        headers = self._build_auth_headers()
        resp = await self._client.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=self._spec.default_timeout,
        )
        if resp.status_code == 401 and self._auth.type == "oauth2":
            refreshed = await self._refresh_token()
            if refreshed:
                headers = self._build_auth_headers()
                resp = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=self._spec.default_timeout,
                )
        resp.raise_for_status()
        return resp

    def _build_auth_headers(self) -> dict[str, str]:
        """Build HTTP headers from auth config + credentials."""
        headers: dict[str, str] = {}
        if self._auth.type == "oauth2":
            if self._access_token:
                prefix = self._auth.header_prefix
                headers[self._auth.header_name] = (
                    f"{prefix} {self._access_token}" if prefix else self._access_token
                )
        elif self._auth.type == "api_key":
            key = self._credentials.get("api_key", "")
            prefix = self._auth.header_prefix
            headers[self._auth.header_name] = (
                f"{prefix} {key}" if prefix else key
            )
        elif self._auth.type == "basic":
            import base64

            user = self._credentials.get("username", "")
            pwd = self._credentials.get("password", "")
            raw = f"{user}:{pwd}".encode()
            encoded = base64.b64encode(raw).decode("ascii")
            headers[self._auth.header_name] = f"Basic {encoded}"
        return headers

    async def _refresh_token(self) -> bool:
        """Refresh OAuth2 token via client_credentials grant."""
        if not self._auth.token_endpoint:
            return False
        client_id = self._credentials.get("client_id")
        client_secret = self._credentials.get("client_secret")
        if not client_id or not client_secret:
            return False

        try:
            resp = await self._client.post(
                self._auth.token_endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=self._spec.default_timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            self._access_token = str(body.get("access_token", ""))
            expires_in = body.get("expires_in")
            if expires_in:
                import time

                self._token_expires_at = time.time() + float(expires_in)
            return True
        except Exception:
            logger.exception("OAuth2 token refresh failed")
            return False

    # -- Write operation helpers -----------------------------------------

    async def _do_create(
        self, resource: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        res_spec = self._spec.resources[resource]
        path = res_spec.path.replace("/{id}", "")
        url = f"{self._spec.base_url}{path}"
        resp = await self._request("POST", url, json_body=data)
        result: dict[str, Any] = resp.json()
        return result

    async def _do_update(
        self, resource: str, record_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        res_spec = self._spec.resources[resource]
        path = res_spec.path.replace("{id}", record_id)
        url = f"{self._spec.base_url}{path}"
        resp = await self._request("PUT", url, json_body=data)
        result: dict[str, Any] = resp.json()
        return result

    async def _do_delete(self, resource: str, record_id: str) -> None:
        res_spec = self._spec.resources[resource]
        path = res_spec.path.replace("{id}", record_id)
        url = f"{self._spec.base_url}{path}"
        await self._request("DELETE", url)

    async def _fetch_single(
        self, resource: str, record_id: str
    ) -> dict[str, Any] | None:
        """Fetch a single record by ID (for before snapshot)."""
        res_spec = self._spec.resources[resource]
        path = res_spec.path.replace("{id}", record_id)
        url = f"{self._spec.base_url}{path}"
        try:
            resp = await self._request("GET", url)
            result: dict[str, Any] = resp.json()
            return result
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    # -- Query/pagination helpers ----------------------------------------

    def _build_query_params(self, query: ReadQuery) -> dict[str, Any]:
        """Build query parameters from a ReadQuery."""
        params: dict[str, Any] = {}
        for k, v in query.filters.items():
            params[k] = v
        if query.fields:
            params["fields"] = ",".join(query.fields)
        pagination = self._spec.pagination
        if pagination:
            if pagination.type == "offset":
                params[pagination.offset_param] = query.offset
                params[pagination.limit_param] = query.limit
            elif pagination.type == "page":
                page = (query.offset // query.limit) + 1 if query.limit > 0 else 1
                params[pagination.page_param] = page
                params[pagination.page_size_param] = query.limit
        else:
            params["limit"] = query.limit
            params["offset"] = query.offset
        return params

    @staticmethod
    def _extract_rows(
        body: dict[str, Any] | list[Any],
        pagination: PaginationSpec | None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Extract rows and total from response body based on pagination config."""
        if isinstance(body, list):
            rows = body
            total = len(rows)
        elif pagination and pagination.data_field:
            rows = body.get(pagination.data_field, [])
            total = body.get(pagination.total_field, len(rows))
        else:
            rows = body.get("data", body.get("rows", []))
            total = body.get("total", len(rows))
        if not isinstance(rows, list):
            rows = []
            total = 0
        return rows, total
