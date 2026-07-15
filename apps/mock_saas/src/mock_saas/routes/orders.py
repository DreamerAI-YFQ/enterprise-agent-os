"""Order CRUD routes — ERP core resource for write-flow demos."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from mock_saas.auth import require_auth
from mock_saas.db import MockSaasDB, get_db
from mock_saas.models import Order, OrderCreate, OrderUpdate

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


def _paginated(rows: list[Order], page: int, page_size: int) -> dict[str, Any]:
    total = len(rows)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "data": rows[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("")
async def list_orders(
    customer_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> dict[str, Any]:
    rows = db.list_orders(customer_id=customer_id, status=status_filter)
    return _paginated(rows, page, page_size)


@router.get("/{oid}")
async def get_order(
    oid: str,
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> Order:
    order = db.get_order(oid)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"order not found: {oid}")
    return order


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_order(
    body: OrderCreate,
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> Order:
    if db.get_customer(body.customer_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown customer_id: {body.customer_id}",
        )
    now = datetime.now(UTC)
    order = Order(
        customer_id=body.customer_id,
        amount=body.amount,
        currency=body.currency,
        status=body.status,
        items=body.items,
        created_at=now,
        updated_at=now,
    )
    return db.create_order(order)


@router.put("/{oid}")
async def update_order(
    oid: str,
    body: OrderUpdate,
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> Order:
    if body.customer_id is not None and db.get_customer(body.customer_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown customer_id: {body.customer_id}",
        )
    updated = db.update_order(
        oid,
        customer_id=body.customer_id,
        amount=body.amount,
        currency=body.currency,
        status=body.status,
        items=body.items,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"order not found: {oid}")
    return updated


@router.delete("/{oid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_order(
    oid: str,
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> None:
    if not db.delete_order(oid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"order not found: {oid}")
