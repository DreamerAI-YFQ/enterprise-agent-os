"""Tests for the knowledge contribution submission + admin review workflow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from eaos.core.auth import create_jwt_token
from eaos.core.config import AppConfig
from eaos.gateway.api.app import create_app
from httpx import ASGITransport, AsyncClient

SECRET = "contrib-test-secret-32bytes!"
TID = UUID("00000000-0000-0000-0000-000000000001")
ADMIN_ID = UUID("00000000-0000-0000-0000-000000000010")
EMP_ID = UUID("00000000-0000-0000-0000-000000000020")


def _config() -> AppConfig:
    return AppConfig(secret_key=SECRET, debug=True)  # type: ignore[call-arg]


def _admin_token() -> str:
    return create_jwt_token(SECRET, ADMIN_ID, TID, "admin")


def _employee_token() -> str:
    return create_jwt_token(SECRET, EMP_ID, TID, "employee")


def _contrib_row(
    *,
    cid: UUID | None = None,
    submitter_id: UUID = EMP_ID,
    status: str = "pending",
    review_comment: str | None = None,
    reviewer_id: UUID | None = None,
    title: str = "测试贡献",
    content: str = "测试内容",
    source_type: str = "manual",
    source_uri: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": str(cid or uuid4()),
        "tenant_id": str(TID),
        "submitter_id": str(submitter_id),
        "source_type": source_type,
        "source_uri": source_uri,
        "title": title,
        "content": content,
        "status": status,
        "reviewer_id": str(reviewer_id) if reviewer_id else None,
        "review_comment": review_comment,
        "submitted_at": "2026-07-05T10:00:00+00:00",
        "reviewed_at": "2026-07-05T11:00:00+00:00" if status != "pending" else None,
        "metadata": metadata or {},
    }


def _mock_db(*, fetch_rows: list[dict[str, Any]] | None = None,
             single_row: dict[str, Any] | None = None) -> Any:
    db: Any = AsyncMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.fetch_one = AsyncMock(return_value=single_row)
    db.execute = AsyncMock(return_value=None)
    return db


def _mock_rag() -> Any:
    rag: Any = AsyncMock()
    rag.ingest = AsyncMock(return_value=[uuid4(), uuid4()])
    return rag


def _build_app(db: Any, rag: Any | None = None) -> Any:
    app = create_app(_config())
    app.state.db = db
    if rag is not None:
        app.state.rag_pipeline = rag
    return app


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- Employee submit ---------------------------------------------------------


@pytest.mark.asyncio
async def test_employee_submit_creates_row() -> None:
    row = _contrib_row(status="pending")
    db = _mock_db(single_row=row)
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/knowledge/contributions",
            json={
                "title": "测试贡献",
                "content": "测试内容",
                "source_type": "manual",
            },
            headers=_auth(_employee_token()),
        )
    assert resp.status_code == 201
    assert db.execute.await_count == 1
    body = resp.json()
    assert body["status"] == "pending"
    assert body["title"] == "测试贡献"


# -- Employee list mine ------------------------------------------------------


@pytest.mark.asyncio
async def test_list_mine_filters_by_submitter() -> None:
    rows = [_contrib_row(), _contrib_row(title="另一条")]
    db = _mock_db(fetch_rows=rows)
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/knowledge/contributions/mine",
            headers=_auth(_employee_token()),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2


# -- Admin list all ----------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_list_returns_all() -> None:
    rows = [
        _contrib_row(),
        _contrib_row(title="doc2"),
        _contrib_row(title="doc3"),
    ]
    db = _mock_db(fetch_rows=rows)
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/admin/contributions",
            headers=_auth(_admin_token()),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 3


@pytest.mark.asyncio
async def test_admin_list_with_status_filter() -> None:
    rows = [_contrib_row(status="pending")]
    db = _mock_db(fetch_rows=rows)
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/admin/contributions?status=pending",
            headers=_auth(_admin_token()),
        )
    assert resp.status_code == 200
    # The fetch call should have been called with "pending" as the second
    # positional arg (after tenant_id).
    args = db.fetch.await_args.args
    assert "pending" in args


# -- Admin approve -----------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_approve_calls_rag_ingest_and_updates() -> None:
    cid = uuid4()
    pending_row = _contrib_row(cid=cid, status="pending")
    approved_row = _contrib_row(cid=cid, status="approved", reviewer_id=ADMIN_ID)
    # First fetch_one returns the pending row (for the review lookup),
    # second returns the updated approved row.
    db: Any = AsyncMock()
    db.fetch = AsyncMock(return_value=[])
    db.fetch_one = AsyncMock(side_effect=[pending_row, approved_row])
    db.execute = AsyncMock(return_value=None)
    rag = _mock_rag()
    app = _build_app(db, rag=rag)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/admin/contributions/{cid}/review",
            json={"decision": "approved"},
            headers=_auth(_admin_token()),
        )
    assert resp.status_code == 200
    assert rag.ingest.await_count == 1
    # UPDATE contribution + INSERT notification.
    assert db.execute.await_count == 2
    body = resp.json()
    assert body["status"] == "approved"


# -- Admin reject ------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_reject_skips_ingest() -> None:
    cid = uuid4()
    pending_row = _contrib_row(cid=cid, status="pending")
    rejected_row = _contrib_row(
        cid=cid,
        status="rejected",
        reviewer_id=ADMIN_ID,
        review_comment="内容不符",
    )
    db: Any = AsyncMock()
    db.fetch = AsyncMock(return_value=[])
    db.fetch_one = AsyncMock(side_effect=[pending_row, rejected_row])
    db.execute = AsyncMock(return_value=None)
    rag = _mock_rag()
    app = _build_app(db, rag=rag)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/admin/contributions/{cid}/review",
            json={"decision": "rejected", "reason": "内容不符"},
            headers=_auth(_admin_token()),
        )
    assert resp.status_code == 200
    # Reject path must NOT call rag.ingest.
    assert rag.ingest.await_count == 0
    # UPDATE contribution + INSERT notification.
    assert db.execute.await_count == 2
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["review_comment"] == "内容不符"


# -- Permissions --------------------------------------------------------------


@pytest.mark.asyncio
async def test_employee_cannot_access_admin_list() -> None:
    db = _mock_db(fetch_rows=[])
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/admin/contributions",
            headers=_auth(_employee_token()),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_no_token_returns_401() -> None:
    db = _mock_db()
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/knowledge/contributions",
            json={"title": "x", "content": "y"},
        )
    assert resp.status_code == 401


# -- State machine ------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_already_decided_returns_409() -> None:
    cid = uuid4()
    approved_row = _contrib_row(cid=cid, status="approved", reviewer_id=ADMIN_ID)
    db = _mock_db(single_row=approved_row)
    rag = _mock_rag()
    app = _build_app(db, rag=rag)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/admin/contributions/{cid}/review",
            json={"decision": "approved"},
            headers=_auth(_admin_token()),
        )
    assert resp.status_code == 409
    # Already-reviewed path must not call rag.ingest.
    assert rag.ingest.await_count == 0


# -- Get detail ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_contribution_detail_returns_200() -> None:
    cid = uuid4()
    row = _contrib_row(cid=cid)
    db = _mock_db(single_row=row)
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/admin/contributions/{cid}",
            headers=_auth(_admin_token()),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(cid)


@pytest.mark.asyncio
async def test_get_contribution_detail_404_when_missing() -> None:
    cid = uuid4()
    db = _mock_db(single_row=None)
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/admin/contributions/{cid}",
            headers=_auth(_admin_token()),
        )
    assert resp.status_code == 404
