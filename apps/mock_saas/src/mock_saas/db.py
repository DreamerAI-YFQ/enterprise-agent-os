"""In-memory store with demo data seed for mock SaaS.

A mock external system does not need cross-restart persistence: every container
start re-seeds deterministic demo data, and tests construct a fresh store per
case. This keeps the service zero-dependency (no sqlite/PG driver) and fully
deterministic. The repository surface is small enough that a real DB backend
could be swapped in later without touching the route layer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import TYPE_CHECKING

from mock_saas.models import (
    Customer,
    Inventory,
    Order,
    OrderItem,
    validate_customer_tier,
    validate_order_status,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

# Demo OAuth2 client registry: client_id -> client_secret.
DEMO_OAUTH_CLIENTS: dict[str, str] = {
    "eaos-client": "eaos-secret",
}

# Demo static API keys.
DEMO_API_KEYS: frozenset[str] = frozenset(
    {
        "eaos-api-key-001",
        "eaos-api-key-002",
    }
)

# Secret used to sign demo JWTs. In a real SaaS this would be a proper key;
# for the mock it is a static value so tests can forge tokens if needed.
DEMO_JWT_SECRET: str = "mock-saas-demo-secret-not-for-production"

# Threshold below which an inventory row is considered low-stock.
LOW_STOCK_THRESHOLD: int = 10


class MockSaasDB:
    """Thread-safe in-memory store for customers, orders, and inventory."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._customers: dict[str, Customer] = {}
        self._orders: dict[str, Order] = {}
        self._inventory: dict[str, Inventory] = {}
        self.seed()

    # ---- seed ----------------------------------------------------------

    def seed(self) -> None:
        """Populate the store with deterministic demo data."""
        with self._lock:
            self._customers.clear()
            self._orders.clear()
            self._inventory.clear()
            self._seed_customers()
            self._seed_orders()
            self._seed_inventory()

    def _seed_customers(self) -> None:
        regions_tiers: list[tuple[str, str, str, str, str]] = [
            ("cus_acme", "ACME 工业有限公司", "华东", "vip", "contact@acme.com"),
            ("cus_globex", "Globex 科技", "华南", "gold", "sales@globex.com"),
            ("cus_initech", "Initech 软件", "华北", "silver", "info@initech.com"),
            ("cus_umbrella", "Umbrella 集团", "华西", "standard", "hello@umbrella.com"),
            ("cus_stark", "Stark 工业", "华中", "vip", "tony@stark.com"),
        ]
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for idx, (cid, name, region, tier, email) in enumerate(regions_tiers):
            self._customers[cid] = Customer(
                id=cid,
                name=name,
                region=region,
                tier=validate_customer_tier(tier),
                contact_email=email,
                created_at=base + timedelta(days=idx),
            )

    def _seed_orders(self) -> None:
        customer_ids = list(self._customers.keys())
        statuses = ["pending", "confirmed", "shipped", "delivered", "cancelled"]
        base = datetime(2026, 3, 1, tzinfo=UTC)
        for i in range(20):
            cid = customer_ids[i % len(customer_ids)]
            status = validate_order_status(statuses[i % len(statuses)])
            items = [
                OrderItem(sku=f"SKU-{(i % 10):04d}", quantity=(i % 5) + 1, unit_price=99.9),
            ]
            created = base + timedelta(days=i)
            oid = f"ord_{i + 1:04d}"
            self._orders[oid] = Order(
                id=oid,
                customer_id=cid,
                amount=round(99.9 * ((i % 5) + 1), 2),
                currency="CNY",
                status=status,
                items=items,
                created_at=created,
                updated_at=created,
            )

    def _seed_inventory(self) -> None:
        rows: list[tuple[str, str, int, str]] = [
            ("SKU-0000", "电机 750W", 120, "华东仓"),
            ("SKU-0001", "电机 1kW", 8, "华东仓"),  # low-stock
            ("SKU-0002", "控制器 V2", 45, "华南仓"),
            ("SKU-0003", "传感器 温度", 5, "华南仓"),  # low-stock
            ("SKU-0004", "伺服舵机", 60, "华北仓"),
            ("SKU-0005", "电源 24V", 3, "华北仓"),  # low-stock
            ("SKU-0006", "线束 A 型", 200, "华西仓"),
            ("SKU-0007", "外壳 铝合金", 30, "华西仓"),
            ("SKU-0008", "PCB 主板", 15, "华中仓"),
            ("SKU-0009", "连接器 RJ45", 500, "华中仓"),
        ]
        base = datetime(2026, 1, 15, tzinfo=UTC)
        for sku, name, qty, wh in rows:
            self._inventory[sku] = Inventory(
                sku=sku,
                product_name=name,
                quantity=qty,
                warehouse=wh,
                updated_at=base,
            )

    # ---- customers -----------------------------------------------------

    def list_customers(
        self, *, region: str | None = None, tier: str | None = None
    ) -> list[Customer]:
        with self._lock:
            return [
                c
                for c in self._customers.values()
                if (region is None or c.region == region)
                and (tier is None or c.tier == tier)
            ]

    def get_customer(self, cid: str) -> Customer | None:
        with self._lock:
            return self._customers.get(cid)

    def create_customer(self, customer: Customer) -> Customer:
        with self._lock:
            if customer.id in self._customers:
                raise ValueError(f"customer already exists: {customer.id}")
            self._customers[customer.id] = customer
            return customer

    def update_customer(
        self, cid: str, *, name: str | None = None, region: str | None = None,
        tier: str | None = None, contact_email: str | None = None,
    ) -> Customer | None:
        with self._lock:
            existing = self._customers.get(cid)
            if existing is None:
                return None
            updated = existing.model_copy(
                update={
                    "name": name or existing.name,
                    "region": region or existing.region,
                    "tier": validate_customer_tier(tier) if tier else existing.tier,
                    "contact_email": contact_email or existing.contact_email,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._customers[cid] = updated
            return updated

    def delete_customer(self, cid: str) -> bool:
        with self._lock:
            return self._customers.pop(cid, None) is not None

    # ---- orders --------------------------------------------------------

    def list_orders(
        self, *, customer_id: str | None = None, status: str | None = None,
    ) -> list[Order]:
        with self._lock:
            return [
                o
                for o in self._orders.values()
                if (customer_id is None or o.customer_id == customer_id)
                and (status is None or o.status == status)
            ]

    def get_order(self, oid: str) -> Order | None:
        with self._lock:
            return self._orders.get(oid)

    def create_order(self, order: Order) -> Order:
        with self._lock:
            if order.id in self._orders:
                raise ValueError(f"order already exists: {order.id}")
            self._orders[order.id] = order
            return order

    def update_order(
        self, oid: str, *, customer_id: str | None = None, amount: float | None = None,
        currency: str | None = None, status: str | None = None,
        items: list[OrderItem] | None = None,
    ) -> Order | None:
        with self._lock:
            existing = self._orders.get(oid)
            if existing is None:
                return None
            update: dict[str, object] = {"updated_at": datetime.now(UTC)}
            if customer_id is not None:
                update["customer_id"] = customer_id
            if amount is not None:
                update["amount"] = amount
            if currency is not None:
                update["currency"] = currency
            if status is not None:
                update["status"] = validate_order_status(status)
            if items is not None:
                update["items"] = items
            updated = existing.model_copy(update=update)
            self._orders[oid] = updated
            return updated

    def delete_order(self, oid: str) -> bool:
        with self._lock:
            return self._orders.pop(oid, None) is not None

    # ---- inventory -----------------------------------------------------

    def list_inventory(
        self, *, warehouse: str | None = None, low_stock: bool = False,
    ) -> list[Inventory]:
        with self._lock:
            return [
                inv
                for inv in self._inventory.values()
                if (warehouse is None or inv.warehouse == warehouse)
                and (not low_stock or inv.quantity < LOW_STOCK_THRESHOLD)
            ]

    def get_inventory(self, sku: str) -> Inventory | None:
        with self._lock:
            return self._inventory.get(sku)

    def update_inventory(
        self, sku: str, *, quantity: int | None = None,
        product_name: str | None = None, warehouse: str | None = None,
    ) -> Inventory | None:
        with self._lock:
            existing = self._inventory.get(sku)
            if existing is None:
                return None
            updated = existing.model_copy(
                update={
                    "quantity": quantity if quantity is not None else existing.quantity,
                    "product_name": product_name or existing.product_name,
                    "warehouse": warehouse or existing.warehouse,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._inventory[sku] = updated
            return updated

    # ---- iteration helpers (for MCP server + tests) -------------------

    def all_orders(self) -> Iterator[Order]:
        with self._lock:
            yield from self._orders.values()


# Module-level singleton used by the FastAPI app and the MCP server wrapper.
_db: MockSaasDB | None = None


def get_db() -> MockSaasDB:
    """Return the process-wide store singleton, seeding on first access."""
    global _db
    if _db is None:
        _db = MockSaasDB()
    return _db


def reset_db() -> MockSaasDB:
    """Drop and re-seed the singleton — used by tests for a clean slate."""
    global _db
    _db = MockSaasDB()
    return _db
