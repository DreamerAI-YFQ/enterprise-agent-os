"""Pydantic models for mock SaaS data — Order, Customer, Inventory.

Mirrors typical ERP/CRM domain objects so the EAOS connectors exercise
realistic create/update/delete flows against this mock external system.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class OrderItem(BaseModel):
    """A line item within an order."""

    sku: str
    quantity: int = Field(ge=1)
    unit_price: float = Field(ge=0)


class Order(BaseModel):
    """ERP order — the primary write target for demo scenarios."""

    id: str = Field(default_factory=lambda: f"ord_{uuid4().hex[:12]}")
    customer_id: str
    amount: float = Field(ge=0)
    currency: str = "CNY"
    status: str = "pending"  # pending/confirmed/shipped/delivered/cancelled
    items: list[OrderItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OrderCreate(BaseModel):
    """Request body for POST /api/v1/orders."""

    customer_id: str
    amount: float = Field(ge=0)
    currency: str = "CNY"
    status: str = "pending"
    items: list[OrderItem] = Field(default_factory=list)


class OrderUpdate(BaseModel):
    """Request body for PUT /api/v1/orders/{id} — all fields optional."""

    customer_id: str | None = None
    amount: float | None = Field(default=None, ge=0)
    currency: str | None = None
    status: str | None = None
    items: list[OrderItem] | None = None


class Customer(BaseModel):
    """CRM customer record."""

    id: str = Field(default_factory=lambda: f"cus_{uuid4().hex[:12]}")
    name: str
    region: str  # 华东/华南/华北/华西/华中
    tier: str = "standard"  # standard/silver/gold/vip
    contact_email: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CustomerCreate(BaseModel):
    """Request body for POST /api/v1/customers."""

    name: str
    region: str
    tier: str = "standard"
    contact_email: str


class CustomerUpdate(BaseModel):
    """Request body for PUT /api/v1/customers/{id} — all fields optional."""

    name: str | None = None
    region: str | None = None
    tier: str | None = None
    contact_email: str | None = None


class Inventory(BaseModel):
    """ERP inventory record keyed by SKU."""

    sku: str
    product_name: str
    quantity: int = Field(ge=0)
    warehouse: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InventoryUpdate(BaseModel):
    """Request body for PUT /api/v1/inventory/{sku}."""

    quantity: int = Field(ge=0)
    product_name: str | None = None
    warehouse: str | None = None


_VALID_ORDER_STATUSES = frozenset(
    {"pending", "confirmed", "shipped", "delivered", "cancelled"}
)
_VALID_TIERS = frozenset({"standard", "silver", "gold", "vip"})


def validate_order_status(status: str) -> str:
    """Return status if valid, else raise ValueError."""
    if status not in _VALID_ORDER_STATUSES:
        raise ValueError(
            f"invalid order status '{status}'; must be one of "
            f"{sorted(_VALID_ORDER_STATUSES)}"
        )
    return status


def validate_customer_tier(tier: str) -> str:
    """Return tier if valid, else raise ValueError."""
    if tier not in _VALID_TIERS:
        raise ValueError(
            f"invalid customer tier '{tier}'; must be one of {sorted(_VALID_TIERS)}"
        )
    return tier
