"""Customer CRUD routes — CRM core resource."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from mock_saas.auth import require_auth
from mock_saas.db import MockSaasDB, get_db
from mock_saas.models import Customer, CustomerCreate, CustomerUpdate

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


def _paginated(rows: list[Customer], page: int, page_size: int) -> dict[str, Any]:
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
async def list_customers(
    region: str | None = Query(default=None),
    tier: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> dict[str, Any]:
    rows = db.list_customers(region=region, tier=tier)
    return _paginated(rows, page, page_size)


@router.get("/{cid}")
async def get_customer(
    cid: str,
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> Customer:
    customer = db.get_customer(cid)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"customer not found: {cid}",
        )
    return customer


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_customer(
    body: CustomerCreate,
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> Customer:
    customer = Customer(
        name=body.name,
        region=body.region,
        tier=body.tier,
        contact_email=body.contact_email,
    )
    return db.create_customer(customer)


@router.put("/{cid}")
async def update_customer(
    cid: str,
    body: CustomerUpdate,
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> Customer:
    updated = db.update_customer(
        cid,
        name=body.name,
        region=body.region,
        tier=body.tier,
        contact_email=body.contact_email,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"customer not found: {cid}",
        )
    return updated


@router.delete("/{cid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer(
    cid: str,
    db: MockSaasDB = Depends(get_db),
    _principal: str = Depends(require_auth),
) -> None:
    if not db.delete_customer(cid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"customer not found: {cid}",
        )
