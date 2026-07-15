"""Inventory routes — ERP stock resource. Read + update only (no create/delete)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from mock_saas.auth import require_auth
from mock_saas.db import MockSaasDB, get_db
from mock_saas.models import Inventory, InventoryUpdate

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


def _paginated(rows: list[Inventory], page: int, page_size: int) -> dict[str, Any]:
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
async def list_inventory(
    warehouse: str | None = Query(default=None),
    low_stock: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> dict[str, Any]:
    rows = db.list_inventory(warehouse=warehouse, low_stock=low_stock)
    return _paginated(rows, page, page_size)


@router.get("/{sku}")
async def get_inventory(
    sku: str,
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> Inventory:
    inv = db.get_inventory(sku)
    if inv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"inventory not found: {sku}",
        )
    return inv


@router.put("/{sku}")
async def update_inventory(
    sku: str,
    body: InventoryUpdate,
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> Inventory:
    updated = db.update_inventory(
        sku,
        quantity=body.quantity,
        product_name=body.product_name,
        warehouse=body.warehouse,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"inventory not found: {sku}",
        )
    return updated
