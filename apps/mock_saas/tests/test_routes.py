"""Unit tests for mock SaaS REST API — CRUD, auth, pagination, errors."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from mock_saas.db import DEMO_API_KEYS, reset_db
from mock_saas.main import create_app


@pytest.fixture(autouse=True)
def _fresh_db() -> None:
    """Re-seed the in-memory store before every test for isolation."""
    reset_db()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def bearer_token(client: TestClient) -> str:
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "eaos-client",
            "client_secret": "eaos-secret",
        },
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


@pytest.fixture()
def auth_headers(bearer_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer_token}"}


@pytest.fixture()
def apikey_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(DEMO_API_KEYS))}


# ============================================================
# Health
# ============================================================


class TestHealth:
    def test_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ============================================================
# OAuth2 token endpoint
# ============================================================


class TestOAuthToken:
    def test_valid_credentials(self, client: TestClient) -> None:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "eaos-client",
                "client_secret": "eaos-secret",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 1800
        assert isinstance(body["access_token"], str)

    def test_wrong_secret(self, client: TestClient) -> None:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "eaos-client",
                "client_secret": "wrong",
            },
        )
        assert resp.status_code == 401

    def test_unknown_client(self, client: TestClient) -> None:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "nope",
                "client_secret": "nope",
            },
        )
        assert resp.status_code == 401

    def test_unsupported_grant(self, client: TestClient) -> None:
        resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "password",
                "client_id": "eaos-client",
                "client_secret": "eaos-secret",
            },
        )
        assert resp.status_code == 400


# ============================================================
# Auth enforcement
# ============================================================


class TestAuthEnforcement:
    def test_no_credentials_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/v1/orders")
        assert resp.status_code == 401

    def test_bearer_token_accepted(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/api/v1/orders", headers=auth_headers)
        assert resp.status_code == 200

    def test_api_key_accepted(
        self, client: TestClient, apikey_headers: dict[str, str]
    ) -> None:
        resp = client.get("/api/v1/orders", headers=apikey_headers)
        assert resp.status_code == 200

    def test_invalid_bearer_rejected(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/orders", headers={"Authorization": "Bearer garbage"}
        )
        assert resp.status_code == 401

    def test_invalid_api_key_rejected(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/orders", headers={"X-API-Key": "wrong-key"}
        )
        assert resp.status_code == 401

    def test_health_does_not_require_auth(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200


# ============================================================
# Orders CRUD
# ============================================================


class TestOrdersCrud:
    def test_list_seeded_orders(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/api/v1/orders", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 20
        assert len(body["data"]) == 20

    def test_list_filter_by_status(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/orders?status=pending", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4  # 20 orders, 5 statuses, 4 each
        assert all(o["status"] == "pending" for o in body["data"])

    def test_list_filter_by_customer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/orders?customer_id=cus_acme", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4  # 20 / 5 customers = 4
        assert all(o["customer_id"] == "cus_acme" for o in body["data"])

    def test_pagination(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/orders?page=1&page_size=5", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 20
        assert len(body["data"]) == 5
        assert body["page"] == 1

        resp2 = client.get(
            "/api/v1/orders?page=4&page_size=5", headers=auth_headers
        )
        body2 = resp2.json()
        assert len(body2["data"]) == 5

    def test_get_order_by_id(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/api/v1/orders/ord_0001", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == "ord_0001"

    def test_get_order_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/api/v1/orders/ord_nope", headers=auth_headers)
        assert resp.status_code == 404

    def test_create_order(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/api/v1/orders",
            headers=auth_headers,
            json={
                "customer_id": "cus_acme",
                "amount": 1000000.0,
                "currency": "CNY",
                "status": "pending",
                "items": [
                    {"sku": "SKU-0000", "quantity": 2, "unit_price": 500000.0}
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["customer_id"] == "cus_acme"
        assert body["amount"] == 1000000.0
        assert body["id"].startswith("ord_")

    def test_create_order_unknown_customer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/api/v1/orders",
            headers=auth_headers,
            json={"customer_id": "cus_nope", "amount": 100.0},
        )
        assert resp.status_code == 422

    def test_update_order(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.put(
            "/api/v1/orders/ord_0001",
            headers=auth_headers,
            json={"status": "confirmed"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "confirmed"

    def test_update_order_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.put(
            "/api/v1/orders/ord_nope",
            headers=auth_headers,
            json={"status": "confirmed"},
        )
        assert resp.status_code == 404

    def test_delete_order(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.delete("/api/v1/orders/ord_0001", headers=auth_headers)
        assert resp.status_code == 204

        # Confirm gone
        resp2 = client.get("/api/v1/orders/ord_0001", headers=auth_headers)
        assert resp2.status_code == 404

    def test_delete_order_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.delete("/api/v1/orders/ord_nope", headers=auth_headers)
        assert resp.status_code == 404


# ============================================================
# Customers CRUD
# ============================================================


class TestCustomersCrud:
    def test_list_seeded(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/api/v1/customers", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 5

    def test_list_filter_by_region(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/customers?region=华东", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["data"][0]["name"] == "ACME 工业有限公司"

    def test_list_filter_by_tier(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/customers?tier=vip", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2  # ACME + Stark

    def test_get_customer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/api/v1/customers/cus_acme", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["tier"] == "vip"

    def test_create_customer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/api/v1/customers",
            headers=auth_headers,
            json={
                "name": "Wayne Enterprises",
                "region": "华东",
                "tier": "gold",
                "contact_email": "bruce@wayne.com",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Wayne Enterprises"
        assert body["tier"] == "gold"
        assert body["id"].startswith("cus_")

    def test_update_customer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.put(
            "/api/v1/customers/cus_acme",
            headers=auth_headers,
            json={"tier": "silver"},
        )
        assert resp.status_code == 200
        assert resp.json()["tier"] == "silver"

    def test_delete_customer(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.delete(
            "/api/v1/customers/cus_acme", headers=auth_headers
        )
        assert resp.status_code == 204
        resp2 = client.get("/api/v1/customers/cus_acme", headers=auth_headers)
        assert resp2.status_code == 404


# ============================================================
# Inventory
# ============================================================


class TestInventory:
    def test_list_seeded(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/api/v1/inventory", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 10

    def test_list_low_stock(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/inventory?low_stock=true", headers=auth_headers
        )
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["total"] == 3  # SKU-0001, SKU-0003, SKU-0005
        for item in body["data"]:
            assert item["quantity"] < 10

    def test_list_filter_warehouse(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/inventory?warehouse=华东仓", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2

    def test_get_inventory(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/inventory/SKU-0000", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["product_name"] == "电机 750W"

    def test_update_inventory(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.put(
            "/api/v1/inventory/SKU-0000",
            headers=auth_headers,
            json={"quantity": 50},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["quantity"] == 50

    def test_update_inventory_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.put(
            "/api/v1/inventory/SKU-nope",
            headers=auth_headers,
            json={"quantity": 50},
        )
        assert resp.status_code == 404
