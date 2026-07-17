"""Tests for HttpApiConnector — spec-driven REST API connector.

Unit tests mock ``httpx.AsyncClient`` to verify protocol-level behavior:
read with pagination, write with before-snapshot, rollback reversal, auth
header construction, OAuth2 token refresh, and error handling.

Integration tests (marked ``integration``) exercise the connector against
the T0 ``mock_saas`` FastAPI app via ``httpx.ASGITransport`` (no live port).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from eaos.data.connector import ReadQuery, WriteOperation
from eaos.data.http_connector import HttpApiConnector
from eaos.data.http_spec import HttpApiSpec, HttpAuth, PaginationSpec, ResourceSpec

TID = UUID("00000000-0000-0000-0000-000000000001")


# ============================================================
# Helpers
# ============================================================


def _make_response(
    status_code: int,
    json_data: Any = None,
    text: str = "",
) -> httpx.Response:
    """Create a real ``httpx.Response`` for unit tests."""
    req = httpx.Request("GET", "https://test.example.com")
    if json_data is not None:
        return httpx.Response(status_code, json=json_data, request=req)
    return httpx.Response(status_code, text=text, request=req)


def _make_spec(
    *,
    pagination: PaginationSpec | None = None,
    health_path: str | None = "/health",
) -> HttpApiSpec:
    """Build a minimal spec with orders + customers resources."""
    return HttpApiSpec(
        base_url="https://saas.example.com",
        resources={
            "orders": ResourceSpec(
                path="/api/v1/orders/{id}",
                methods=["GET", "POST", "PUT", "DELETE"],
                id_field="id",
                schema={
                    "description": "ERP orders",
                    "columns": [
                        {"name": "id", "type": "string"},
                        {"name": "amount", "type": "float"},
                    ],
                },
                access_mode="read_write",
            ),
            "customers": ResourceSpec(
                path="/api/v1/customers/{id}",
                methods=["GET", "POST", "PUT", "DELETE"],
                id_field="id",
                schema={"description": "CRM customers", "columns": []},
                access_mode="read_write",
            ),
            "inventory": ResourceSpec(
                path="/api/v1/inventory/{id}",
                methods=["GET"],
                id_field="sku",
                schema={"description": "Stock levels", "columns": []},
                access_mode="read",
            ),
        },
        health_check_path=health_path,
        pagination=pagination,
    )


def _make_client(
    side_effect: Any = None,
    post_return: httpx.Response | None = None,
) -> Any:
    """Build a mock ``httpx.AsyncClient`` with scripted responses.

    ``side_effect`` is an async callable or list for ``client.request``.
    ``post_return`` sets the response for ``client.post`` (OAuth2 refresh).
    """
    mock_client: Any = MagicMock(spec=httpx.AsyncClient)
    mock_client.request = AsyncMock(side_effect=side_effect)
    if post_return is not None:
        mock_client.post = AsyncMock(return_value=post_return)
    else:
        mock_client.post = AsyncMock(
            return_value=_make_response(200, {"access_token": "new-token"})
        )
    return mock_client


def _oauth2_auth() -> HttpAuth:
    return HttpAuth(
        type="oauth2",
        token_endpoint="https://saas.example.com/oauth/token",
        header_name="Authorization",
        header_prefix="Bearer",
    )


def _api_key_auth() -> HttpAuth:
    return HttpAuth(type="api_key", header_name="X-API-Key", header_prefix="")


def _basic_auth() -> HttpAuth:
    return HttpAuth(type="basic", header_name="Authorization", header_prefix="")


# ============================================================
# list_resources
# ============================================================


class TestListResources:
    async def test_returns_from_spec(self) -> None:
        spec = _make_spec()
        conn = HttpApiConnector(spec, _oauth2_auth(), _make_client(), {"access_token": "t"})

        resources = await conn.list_resources(TID)
        names = [r.name for r in resources]
        assert "orders" in names
        assert "customers" in names
        assert "inventory" in names
        inv = next(r for r in resources if r.name == "inventory")
        assert inv.access_mode == "read"

    async def test_no_http_call(self) -> None:
        """list_resources is spec-driven — no HTTP request should be made."""
        mock_client = _make_client(side_effect=AssertionError("should not call HTTP"))
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), mock_client, {"access_token": "t"})
        resources = await conn.list_resources(TID)
        assert len(resources) == 3
        mock_client.request.assert_not_called()


# ============================================================
# read
# ============================================================


class TestRead:
    async def test_read_basic(self) -> None:
        spec = _make_spec()
        resp = _make_response(200, {"data": [{"id": "ord_1", "amount": 100}], "total": 1})
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        result = await conn.read(TID, "orders", ReadQuery(limit=10, offset=0))
        assert result.total == 1
        assert result.rows[0]["id"] == "ord_1"

        call = mock_client.request.call_args
        assert call.kwargs["params"]["limit"] == 10
        assert call.kwargs["params"]["offset"] == 0

    async def test_read_with_offset_pagination(self) -> None:
        spec = _make_spec(pagination=PaginationSpec(type="offset"))
        resp = _make_response(200, {"data": [{"id": "ord_1"}], "total": 50})
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        await conn.read(TID, "orders", ReadQuery(limit=20, offset=40))
        call = mock_client.request.call_args
        assert call.kwargs["params"]["offset"] == 40
        assert call.kwargs["params"]["limit"] == 20

    async def test_read_with_page_pagination(self) -> None:
        spec = _make_spec(pagination=PaginationSpec(type="page"))
        resp = _make_response(200, {"data": [{"id": "ord_1"}], "total": 50})
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        await conn.read(TID, "orders", ReadQuery(limit=10, offset=20))
        call = mock_client.request.call_args
        assert call.kwargs["params"]["page"] == 3
        assert call.kwargs["params"]["page_size"] == 10

    async def test_read_unknown_resource(self) -> None:
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), _make_client(), {"access_token": "t"})
        result = await conn.read(TID, "unknown", ReadQuery())
        assert result.rows == []
        assert result.total == 0

    async def test_read_http_error_returns_empty(self) -> None:
        resp = _make_response(500, text="Internal Server Error")
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), mock_client, {"access_token": "t"})
        result = await conn.read(TID, "orders", ReadQuery())
        assert result.rows == []
        assert result.total == 0

    async def test_read_with_filters(self) -> None:
        spec = _make_spec()
        resp = _make_response(200, {"data": [], "total": 0})
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        await conn.read(TID, "orders", ReadQuery(filters={"customer_id": "cus_1"}))
        call = mock_client.request.call_args
        assert call.kwargs["params"]["customer_id"] == "cus_1"

    async def test_read_with_exact_id_uses_record_endpoint(self) -> None:
        spec = _make_spec()
        resp = _make_response(200, {"id": "ord_1", "amount": 100})
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(
            spec,
            _oauth2_auth(),
            mock_client,
            {"access_token": "t"},
        )

        result = await conn.read(
            TID,
            "orders",
            ReadQuery(filters={"id": "ord_1"}),
        )

        assert result.total == 1
        assert result.rows == [{"id": "ord_1", "amount": 100}]
        call = mock_client.request.call_args
        assert call.args[0] == "GET"
        assert call.args[1].endswith("/api/v1/orders/ord_1")
        assert call.kwargs["params"] is None

    async def test_read_list_body_without_data_field(self) -> None:
        """When response body is a bare list (no pagination data_field), extract directly."""
        spec = _make_spec()  # no pagination → else branch
        resp = _make_response(200, [{"id": "ord_1"}, {"id": "ord_2"}])
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})
        result = await conn.read(TID, "orders", ReadQuery())
        assert result.total == 2
        assert len(result.rows) == 2


# ============================================================
# write
# ============================================================


class TestWrite:
    async def test_create(self) -> None:
        spec = _make_spec()
        post_resp = _make_response(201, {"id": "ord_new", "amount": 200})
        mock_client = _make_client(side_effect=[post_resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        result = await conn.write(
            TID, "orders", WriteOperation(operation="create", data={"amount": 200})
        )
        assert result.success is True
        assert result.before is None
        assert result.after is not None
        assert result.after["id"] == "ord_new"

        call = mock_client.request.call_args
        assert call.args[0] == "POST"
        assert call.args[1].endswith("/api/v1/orders")

    async def test_update_with_before_snapshot(self) -> None:
        spec = _make_spec()
        before_resp = _make_response(200, {"id": "ord_1", "amount": 100})
        put_resp = _make_response(200, {"id": "ord_1", "amount": 200})
        mock_client = _make_client(side_effect=[before_resp, put_resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        result = await conn.write(
            TID,
            "orders",
            WriteOperation(operation="update", record_id="ord_1", data={"amount": 200}),
        )
        assert result.success is True
        assert result.before is not None
        assert result.before["amount"] == 100
        assert result.after is not None
        assert result.after["amount"] == 200

        # First call: GET (before snapshot), second: PUT
        calls = mock_client.request.call_args_list
        assert calls[0].args[0] == "GET"
        assert calls[1].args[0] == "PUT"
        assert calls[1].args[1].endswith("/api/v1/orders/ord_1")

    async def test_delete_with_before_snapshot(self) -> None:
        spec = _make_spec()
        before_resp = _make_response(200, {"id": "ord_1", "amount": 100})
        del_resp = _make_response(204)
        mock_client = _make_client(side_effect=[before_resp, del_resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        result = await conn.write(
            TID, "orders", WriteOperation(operation="delete", record_id="ord_1")
        )
        assert result.success is True
        assert result.before is not None
        assert result.before["amount"] == 100
        assert result.after is None

        calls = mock_client.request.call_args_list
        assert calls[0].args[0] == "GET"
        assert calls[1].args[0] == "DELETE"

    async def test_update_record_not_found(self) -> None:
        spec = _make_spec()
        not_found = _make_response(404, text="not found")
        mock_client = _make_client(
            side_effect=httpx.HTTPStatusError(
                "404",
                request=httpx.Request("GET", "https://saas.example.com/api/v1/orders/xxx"),
                response=not_found,
            )
        )
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        result = await conn.write(
            TID, "orders", WriteOperation(operation="update", record_id="xxx", data={})
        )
        assert result.success is False
        assert "not found" in (result.error or "").lower()

    async def test_write_read_only_resource(self) -> None:
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), _make_client(), {"access_token": "t"})
        result = await conn.write(
            TID, "inventory", WriteOperation(operation="create", data={"qty": 10})
        )
        assert result.success is False
        assert "read-only" in (result.error or "")

    async def test_write_unknown_resource(self) -> None:
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), _make_client(), {"access_token": "t"})
        result = await conn.write(
            TID, "unknown", WriteOperation(operation="create", data={})
        )
        assert result.success is False
        assert "unknown resource" in (result.error or "")

    async def test_write_http_error_returns_failure(self) -> None:
        spec = _make_spec()
        before_resp = _make_response(200, {"id": "ord_1", "amount": 100})
        error_resp = _make_response(400, text="Bad Request")

        async def side_effect(method: str, url: str, **kwargs: Any) -> httpx.Response:
            if method == "GET":
                return before_resp
            raise httpx.HTTPStatusError(
                "400",
                request=httpx.Request(method, url),
                response=error_resp,
            )

        mock_client = _make_client(side_effect=side_effect)
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        result = await conn.write(
            TID, "orders", WriteOperation(operation="update", record_id="ord_1", data={})
        )
        assert result.success is False
        assert "HTTP 400" in (result.error or "")
        assert result.before is not None


# ============================================================
# rollback
# ============================================================


class TestRollback:
    async def test_rollback_create_deletes(self) -> None:
        spec = _make_spec()
        del_resp = _make_response(204)
        mock_client = _make_client(side_effect=[del_resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        await conn.rollback(
            TID,
            {"operation": "create", "resource": "orders", "record_id": "ord_1", "before": None},
        )
        call = mock_client.request.call_args
        assert call.args[0] == "DELETE"
        assert call.args[1].endswith("/api/v1/orders/ord_1")

    async def test_rollback_update_restores(self) -> None:
        spec = _make_spec()
        put_resp = _make_response(200, {"id": "ord_1", "amount": 100})
        mock_client = _make_client(side_effect=[put_resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        before = {"id": "ord_1", "amount": 100}
        await conn.rollback(
            TID,
            {"operation": "update", "resource": "orders", "record_id": "ord_1", "before": before},
        )
        call = mock_client.request.call_args
        assert call.args[0] == "PUT"
        assert call.kwargs["json"] == before

    async def test_rollback_delete_recreates(self) -> None:
        spec = _make_spec()
        post_resp = _make_response(201, {"id": "ord_1"})
        mock_client = _make_client(side_effect=[post_resp])
        conn = HttpApiConnector(spec, _oauth2_auth(), mock_client, {"access_token": "t"})

        before = {"id": "ord_1", "amount": 100}
        await conn.rollback(
            TID,
            {"operation": "delete", "resource": "orders", "record_id": "ord_1", "before": before},
        )
        call = mock_client.request.call_args
        assert call.args[0] == "POST"
        assert call.kwargs["json"] == before

    async def test_rollback_swallows_exception(self) -> None:
        """Rollback should log but not raise — used in compensating transactions."""
        mock_client = _make_client(side_effect=Exception("network down"))
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), mock_client, {"access_token": "t"})
        # Should not raise
        await conn.rollback(
            TID,
            {"operation": "create", "resource": "orders", "record_id": "ord_1", "before": None},
        )


# ============================================================
# describe_schema
# ============================================================


class TestDescribeSchema:
    async def test_returns_schema_from_spec(self) -> None:
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), _make_client(), {"access_token": "t"})
        schema = await conn.describe_schema(TID, "orders")
        assert schema.table_name == "orders"
        assert len(schema.columns) == 2
        assert schema.columns[0]["name"] == "id"

    async def test_unknown_resource_raises(self) -> None:
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), _make_client(), {"access_token": "t"})
        with pytest.raises(ValueError, match="unknown resource"):
            await conn.describe_schema(TID, "nope")


# ============================================================
# health_check
# ============================================================


class TestHealthCheck:
    async def test_healthy(self) -> None:
        resp = _make_response(200, {"status": "ok"})
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), mock_client, {"access_token": "t"})
        assert await conn.health_check() is True

    async def test_unhealthy(self) -> None:
        resp = _make_response(503, text="Service Unavailable")
        mock_client = _make_client(
            side_effect=httpx.HTTPStatusError(
                "503",
                request=httpx.Request("GET", "https://saas.example.com/health"),
                response=resp,
            )
        )
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), mock_client, {"access_token": "t"})
        assert await conn.health_check() is False

    async def test_network_error(self) -> None:
        mock_client = _make_client(side_effect=ConnectionError("refused"))
        conn = HttpApiConnector(_make_spec(), _oauth2_auth(), mock_client, {"access_token": "t"})
        assert await conn.health_check() is False


# ============================================================
# auth
# ============================================================


class TestAuth:
    async def test_oauth2_bearer_header(self) -> None:
        resp = _make_response(200, {"data": [], "total": 0})
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(
            _make_spec(), _oauth2_auth(), mock_client, {"access_token": "my-token"}
        )
        await conn.read(TID, "orders", ReadQuery())
        call = mock_client.request.call_args
        assert call.kwargs["headers"]["Authorization"] == "Bearer my-token"

    async def test_api_key_header(self) -> None:
        resp = _make_response(200, {"data": [], "total": 0})
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(
            _make_spec(), _api_key_auth(), mock_client, {"api_key": "secret-key"}
        )
        await conn.read(TID, "orders", ReadQuery())
        call = mock_client.request.call_args
        assert call.kwargs["headers"]["X-API-Key"] == "secret-key"

    async def test_basic_auth_header(self) -> None:
        import base64

        resp = _make_response(200, {"data": [], "total": 0})
        mock_client = _make_client(side_effect=[resp])
        conn = HttpApiConnector(
            _make_spec(), _basic_auth(), mock_client, {"username": "u", "password": "p"}
        )
        await conn.read(TID, "orders", ReadQuery())
        call = mock_client.request.call_args
        expected = base64.b64encode(b"u:p").decode("ascii")
        assert call.kwargs["headers"]["Authorization"] == f"Basic {expected}"

    async def test_oauth2_refresh_on_401(self) -> None:
        """On 401, connector refreshes OAuth2 token and retries the request."""
        unauthorized = _make_response(401, text="token expired")
        ok = _make_response(200, {"data": [{"id": "ord_1"}], "total": 1})
        mock_client = _make_client(side_effect=[unauthorized, ok])
        # Start with an expired token
        conn = HttpApiConnector(
            _make_spec(),
            _oauth2_auth(),
            mock_client,
            {"access_token": "old-token", "client_id": "c", "client_secret": "s"},
        )

        result = await conn.read(TID, "orders", ReadQuery())
        assert result.total == 1

        # Should have called request twice (401 + retry) and post once (refresh)
        assert mock_client.request.call_count == 2
        assert mock_client.post.call_count == 1

        # The retried request should use the new token
        retry_call = mock_client.request.call_args_list[1]
        assert retry_call.kwargs["headers"]["Authorization"] == "Bearer new-token"

    async def test_oauth2_refresh_no_endpoint(self) -> None:
        """Without token_endpoint, 401 raises HTTPStatusError → read() returns empty."""
        auth = HttpAuth(type="oauth2", token_endpoint=None)
        unauthorized = _make_response(401, text="unauthorized")
        mock_client = _make_client(side_effect=[unauthorized])
        conn = HttpApiConnector(_make_spec(), auth, mock_client, {"access_token": "t"})
        result = await conn.read(TID, "orders", ReadQuery())
        assert result.rows == []
        assert result.total == 0


# ============================================================
# _extract_rows (static method)
# ============================================================


class TestExtractRows:
    def test_list_body(self) -> None:
        rows, total = HttpApiConnector._extract_rows([{"id": 1}, {"id": 2}], None)
        assert len(rows) == 2
        assert total == 2

    def test_dict_with_data_field(self) -> None:
        body = {"data": [{"id": 1}], "total": 50}
        rows, total = HttpApiConnector._extract_rows(body, None)
        assert len(rows) == 1
        assert total == 50

    def test_dict_with_pagination_spec(self) -> None:
        pagination = PaginationSpec(type="offset", data_field="items", total_field="count")
        body = {"items": [{"id": 1}], "count": 99}
        rows, total = HttpApiConnector._extract_rows(body, pagination)
        assert len(rows) == 1
        assert total == 99

    def test_non_list_rows_returns_empty(self) -> None:
        rows, total = HttpApiConnector._extract_rows({"data": "not-a-list"}, None)
        assert rows == []
        assert total == 0


# ============================================================
# Integration tests (require mock_saas ASGI app)
# ============================================================


def _mock_saas_spec() -> HttpApiSpec:
    """Spec matching the T0 mock_saas REST API."""
    return HttpApiSpec(
        base_url="http://testserver",
        resources={
            "orders": ResourceSpec(
                path="/api/v1/orders/{id}",
                methods=["GET", "POST", "PUT", "DELETE"],
                id_field="id",
                schema={"columns": []},
                access_mode="read_write",
            ),
            "customers": ResourceSpec(
                path="/api/v1/customers/{id}",
                methods=["GET", "POST", "PUT", "DELETE"],
                id_field="id",
                schema={"columns": []},
                access_mode="read_write",
            ),
        },
        health_check_path="/health",
        pagination=PaginationSpec(type="page", data_field="data", total_field="total"),
    )


@pytest.mark.integration
class TestHttpConnectorIntegration:
    """End-to-end tests against the mock_saas FastAPI app via ASGITransport."""

    @staticmethod
    def _make_client() -> httpx.AsyncClient:
        from mock_saas.db import reset_db
        from mock_saas.main import create_app

        reset_db()
        app = create_app()
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")

    async def test_health_check(self) -> None:
        client = self._make_client()
        try:
            auth = HttpAuth(type="api_key", header_name="X-API-Key", header_prefix="")
            conn = HttpApiConnector(
                _mock_saas_spec(), auth, client, {"api_key": "eaos-api-key-001"}
            )
            assert await conn.health_check() is True
        finally:
            await client.aclose()

    async def test_read_orders(self) -> None:
        client = self._make_client()
        try:
            auth = HttpAuth(type="api_key", header_name="X-API-Key", header_prefix="")
            conn = HttpApiConnector(
                _mock_saas_spec(), auth, client, {"api_key": "eaos-api-key-001"}
            )
            result = await conn.read(TID, "orders", ReadQuery(limit=5, offset=0))
            assert result.total > 0
            assert len(result.rows) <= 5
        finally:
            await client.aclose()

    async def test_full_crud_lifecycle(self) -> None:
        """Create → read → update → delete → verify gone."""
        client = self._make_client()
        try:
            auth = HttpAuth(type="api_key", header_name="X-API-Key", header_prefix="")
            conn = HttpApiConnector(
                _mock_saas_spec(), auth, client, {"api_key": "eaos-api-key-001"}
            )

            # 1. Create an order
            create_result = await conn.write(
                TID,
                "orders",
                WriteOperation(
                    operation="create",
                    data={
                        "customer_id": "cus_acme",
                        "amount": 500.0,
                        "currency": "CNY",
                        "status": "pending",
                    },
                ),
            )
            assert create_result.success is True
            assert create_result.after is not None
            order_id = create_result.after["id"]

            # 2. Read it back
            read_result = await conn.read(
                TID, "orders", ReadQuery(filters={"customer_id": "cus_acme"})
            )
            assert any(r["id"] == order_id for r in read_result.rows)

            # 3. Update it
            update_result = await conn.write(
                TID,
                "orders",
                WriteOperation(
                    operation="update",
                    record_id=order_id,
                    data={"status": "confirmed"},
                ),
            )
            assert update_result.success is True
            assert update_result.before is not None
            assert update_result.before["status"] == "pending"
            assert update_result.after is not None
            assert update_result.after["status"] == "confirmed"

            # 4. Delete it
            delete_result = await conn.write(
                TID, "orders", WriteOperation(operation="delete", record_id=order_id)
            )
            assert delete_result.success is True
            assert delete_result.before is not None
            assert delete_result.before["status"] == "confirmed"

            # 5. Rollback the delete (recreate)
            await conn.rollback(
                TID,
                {
                    "operation": "delete",
                    "resource": "orders",
                    "record_id": order_id,
                    "before": delete_result.before,
                },
            )
            # Verify an order was recreated (mock_saas assigns a new ID on POST)
            read_after_rollback = await conn.read(
                TID, "orders", ReadQuery(filters={"customer_id": "cus_acme"})
            )
            assert any(r["amount"] == 500.0 for r in read_after_rollback.rows)
        finally:
            await client.aclose()

    async def test_oauth2_flow(self) -> None:
        """Exercise OAuth2 client_credentials → token → authenticated read."""
        client = self._make_client()
        try:
            auth = HttpAuth(
                type="oauth2",
                token_endpoint="http://testserver/oauth/token",
                header_name="Authorization",
                header_prefix="Bearer",
            )
            conn = HttpApiConnector(
                _mock_saas_spec(),
                auth,
                client,
                {"client_id": "eaos-client", "client_secret": "eaos-secret"},
            )
            # _refresh_token is called manually since we don't have an initial token
            refreshed = await conn._refresh_token()
            assert refreshed is True
            assert conn._access_token is not None

            result = await conn.read(TID, "orders", ReadQuery(limit=5))
            assert result.total > 0
        finally:
            await client.aclose()
