"""Admin/governance API routes — triggers, audit, policies, quotas, approvals, spans.

All /admin/* routes require the admin role via require_admin. Governance
components (ambient_monitor, policy_engine, cost_governor, approval_gate,
trace_query) are read from app.state; routes return 501 for operations not
supported by the backing Protocol (audit-log query, quota update).
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — FastAPI needs datetime at runtime
from typing import TYPE_CHECKING, Any
from uuid import UUID  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal  # noqa: TC002 — runtime for FastAPI type hints
from eaos.gateway.api.deps import get_db, get_principal
from eaos.infra.db.base import DbClient  # noqa: TC002 — runtime for FastAPI type hints
from fastapi import APIRouter, Depends, HTTPException, Query, Request  # noqa: TC002
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

router = APIRouter(prefix="/admin")


# -- Auth dependency ---------------------------------------------------------


async def require_admin(
    principal: Principal = Depends(get_principal),  # noqa: B008
) -> Principal:
    """Reject non-admin principals with 403. super_admin is treated as admin."""
    if principal.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="admin role required")
    return principal


def require_permission(
    resource: str,
    action: str,
) -> Callable[..., Awaitable[Principal]]:
    """Build a dependency that checks iam.permissions for fine-grained RBAC.

    Admins short-circuit to allow-all. Non-admin roles must have a matching
    row in iam.permissions (or be service accounts). Usage::

        @router.post("/skills/{id}/publish")
        async def publish_skill(
            principal: Principal = Depends(require_permission("skill", "publish")),
        ):
            ...
    """

    async def _check(
        principal: Principal = Depends(get_principal),  # noqa: B008
    ) -> Principal:
        if principal.is_service_account or principal.role in ("admin", "super_admin"):
            return principal
        # Look up iam.permissions for this role × resource × action.
        from eaos.core.auth import get_global_auth

        evaluator = get_global_auth()
        if evaluator is None:
            # No evaluator wired — fall back to admin-only.
            raise HTTPException(status_code=403, detail="permission evaluator not configured")
        allowed = await evaluator.check(principal, resource, action)
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail=f"permission denied: {resource}:{action} required",
            )
        return principal

    return _check


# -- Request bodies ----------------------------------------------------------


class TriggerCreateRequest(BaseModel):
    agent_id: UUID
    trigger_type: str
    condition: dict[str, Any]
    notify_channel: str
    interval_sec: int = 300


class PolicyCreateRequest(BaseModel):
    name: str
    version: str
    content: dict[str, Any]


class ApprovalActionRequest(BaseModel):
    reason: str | None = None


class WriteRollbackRequest(BaseModel):
    reason: str = "manual compensating rollback"


class ApprovalListResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


# -- Helpers -----------------------------------------------------------------


def _component(request: Request, name: str) -> Any:
    """Fetch a governance component from app.state; 501 if not wired."""
    component = getattr(request.app.state, name, None)
    if component is None:
        raise HTTPException(
            status_code=501,
            detail=f"{name} not configured on this instance",
        )
    return component


# -- Triggers ----------------------------------------------------------------


@router.get("/triggers", tags=["admin"])
async def list_triggers(
    request: Request,
    agent_id: UUID | None = Query(default=None),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, Any]]:
    monitor = _component(request, "ambient_monitor")
    triggers = await monitor.list_triggers(principal.tenant_id, agent_id)
    return [asdict(t) for t in triggers]


@router.post("/triggers", tags=["admin"], status_code=201)
async def create_trigger(
    body: TriggerCreateRequest,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    from eaos.agent.ambient import AmbientTrigger, TriggerConfig

    valid = {t.value: t for t in AmbientTrigger}
    trigger_type = valid.get(body.trigger_type)
    if trigger_type is None:
        raise HTTPException(status_code=422, detail=f"invalid trigger_type: {body.trigger_type}")
    config = TriggerConfig(
        trigger_type=trigger_type,
        agent_id=body.agent_id,
        condition=body.condition,
        notify_channel=body.notify_channel,
        interval_sec=body.interval_sec,
    )
    monitor = _component(request, "ambient_monitor")
    trigger_id = await monitor.register_trigger(principal.tenant_id, config)
    return {"trigger_id": str(trigger_id)}


@router.delete("/triggers/{trigger_id}", tags=["admin"], status_code=204)
async def delete_trigger(
    trigger_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> None:
    monitor = _component(request, "ambient_monitor")
    await monitor.unregister_trigger(trigger_id, principal.tenant_id)


# -- Audit logs --------------------------------------------------------------


def _audit_where(
    tenant_id: UUID,
    user_id: UUID | None,
    action: str | None,
    start_time: datetime | None,
    end_time: datetime | None,
) -> tuple[str, list[Any]]:
    where_clauses: list[str] = ["tenant_id = :p0"]
    params: list[Any] = [tenant_id]
    idx = 1
    if user_id is not None:
        where_clauses.append(f"actor_id = :p{idx}")
        params.append(user_id)
        idx += 1
    if action is not None:
        where_clauses.append(f"action = :p{idx}")
        params.append(action)
        idx += 1
    if start_time is not None:
        where_clauses.append(f"created_at >= :p{idx}")
        params.append(start_time)
        idx += 1
    if end_time is not None:
        where_clauses.append(f"created_at <= :p{idx}")
        params.append(end_time)
        idx += 1
    return " AND ".join(where_clauses), params


def _serialize_audit_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    for key in ("actor_id", "resource_id"):
        val = item.get(key)
        if val is not None:
            item[key] = str(val)
    ts = item.get("created_at")
    if ts is not None and hasattr(ts, "isoformat"):
        item["created_at"] = ts.isoformat()
    return item


@router.get("/audit-logs", tags=["admin"])
async def list_audit_logs(
    user_id: UUID | None = Query(default=None),  # noqa: B008
    action: str | None = Query(default=None),  # noqa: B008
    start_time: datetime | None = Query(default=None),  # noqa: B008
    end_time: datetime | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    where_sql, params = _audit_where(principal.tenant_id, user_id, action, start_time, end_time)
    count_row = await db.fetch_one(
        f"SELECT COUNT(*) AS cnt FROM harness.audit_logs WHERE {where_sql}",
        *params,
    )
    total = int(count_row["cnt"]) if count_row else 0

    page_params = params + [limit, offset]
    idx_limit = len(page_params) - 2
    idx_offset = len(page_params) - 1
    rows = await db.fetch(
        f"SELECT id, actor_type, actor_id, action, resource_type, "
        f"resource_id, detail, ip_address::text AS ip_address, created_at "
        f"FROM harness.audit_logs WHERE {where_sql} "
        f"ORDER BY created_at DESC "
        f"LIMIT :p{idx_limit} OFFSET :p{idx_offset}",
        *page_params,
    )
    items = [_serialize_audit_row(r) for r in rows] if rows else []

    # C13/Fix-B3: Fallback to harness.write_audit if audit_logs is empty.
    # WritePipeline logs to write_audit (Phase 7 T7), not audit_logs.
    if not items:
        wa_rows = await db.fetch(
            "SELECT id, tool_name AS action, resource AS resource_type, "
            "operation, success, error, rolled_back, created_at "
            "FROM harness.write_audit WHERE tenant_id = :p0 "
            "ORDER BY created_at DESC LIMIT :p1 OFFSET :p2",
            principal.tenant_id,
            limit,
            offset,
        )
        if wa_rows:
            for r in wa_rows:
                item = {
                    "id": str(r["id"]),
                    "actor_type": "agent",
                    "actor_id": None,
                    "action": str(r.get("action", "")),
                    "resource_type": str(r.get("resource_type", "")),
                    "resource_id": None,
                    "detail": {
                        "operation": r.get("operation"),
                        "success": r.get("success"),
                        "error": r.get("error"),
                        "rolled_back": r.get("rolled_back"),
                    },
                    "ip_address": None,
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                }
                items.append(item)
            count_row = await db.fetch_one(
                "SELECT COUNT(*) AS cnt FROM harness.write_audit WHERE tenant_id = :p0",
                principal.tenant_id,
            )
            total = int(count_row["cnt"]) if count_row else 0

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/write-audits/{audit_id}", tags=["admin"])
async def get_write_audit(
    audit_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Return the full evidence row for one governed write."""
    row = await db.fetch_one(
        """SELECT id, tenant_id, principal_id, tool_name, resource, operation,
                  before_state, after_state, approval_id, trace_id, session_id,
                  idempotency_key, success, error, rolled_back,
                  rollback_reason, created_at
           FROM harness.write_audit
           WHERE id = :p0 AND tenant_id = :p1""",
        audit_id,
        principal.tenant_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="write audit not found")
    return dict(row)


@router.post("/write-audits/{audit_id}/rollback", tags=["admin"])
async def rollback_write_audit(
    audit_id: UUID,
    body: WriteRollbackRequest,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    """Run and verify the connector-specific compensating transaction."""
    from eaos.core.errors import NotFoundError

    pipeline = _component(request, "write_pipeline")
    try:
        outcome = await pipeline.rollback_audit(audit_id, principal.tenant_id, body.reason)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "audit_id": str(audit_id),
        "success": outcome.success,
        "rolled_back": outcome.rolled_back,
        "rollback_error": outcome.rollback_error,
        "approval_id": str(outcome.approval_id) if outcome.approval_id else None,
    }


@router.get("/audit-logs/export", tags=["admin"])
async def export_audit_logs(
    user_id: UUID | None = Query(default=None),  # noqa: B008
    action: str | None = Query(default=None),  # noqa: B008
    start_time: datetime | None = Query(default=None),  # noqa: B008
    end_time: datetime | None = Query(default=None),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> StreamingResponse:
    where_sql, params = _audit_where(principal.tenant_id, user_id, action, start_time, end_time)
    rows = await db.fetch(
        f"SELECT id, actor_type, actor_id, action, resource_type, "
        f"resource_id, detail, ip_address::text AS ip_address, created_at "
        f"FROM harness.audit_logs WHERE {where_sql} "
        f"ORDER BY created_at DESC LIMIT 10000",
        *params,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "timestamp",
            "actor_type",
            "actor_id",
            "action",
            "resource_type",
            "resource_id",
            "ip_address",
            "detail",
        ]
    )
    for row in rows or []:
        ts = row["created_at"]
        ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        writer.writerow(
            [
                row["id"],
                ts_str,
                row["actor_type"],
                str(row["actor_id"]),
                row["action"],
                row["resource_type"],
                str(row["resource_id"]) if row["resource_id"] else "",
                row["ip_address"] or "",
                str(row["detail"]) if row["detail"] else "",
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit-logs.csv"},
    )


# -- Policies ----------------------------------------------------------------


@router.get("/policies", tags=["admin"])
async def list_policies(
    request: Request,
    name: str = Query(...),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, Any]]:
    engine = _component(request, "policy_engine")
    policies = await engine.list_versions(name, principal.tenant_id)
    return [asdict(p) for p in policies]


@router.post("/policies", tags=["admin"], status_code=201)
async def publish_policy(
    body: PolicyCreateRequest,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    from eaos.harness.policy import Policy, PolicyStatus

    policy = Policy(
        name=body.name,
        version=body.version,
        content=body.content,
        status=PolicyStatus.DRAFT,
        tenant_id=principal.tenant_id,
    )
    engine = _component(request, "policy_engine")
    await engine.publish(policy)
    return {"name": body.name, "version": body.version}


@router.post("/policies/{name}/activate", tags=["admin"])
async def activate_policy(
    name: str,
    request: Request,
    version: str = Query(...),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    engine = _component(request, "policy_engine")
    await engine.activate(name, version)
    return {"name": name, "version": version, "status": "active"}


@router.post("/policies/{name}/rollback", tags=["admin"])
async def rollback_policy(
    name: str,
    request: Request,
    version: str = Query(...),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    engine = _component(request, "policy_engine")
    await engine.rollback(name, version)
    return {"name": name, "version": version, "status": "rollback"}


# -- Quotas ------------------------------------------------------------------


@router.get("/quotas", tags=["admin"])
async def get_quota(
    request: Request,
    scope: str = Query(...),  # noqa: B008
    owner_id: UUID | None = Query(default=None),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    from eaos.harness.cost.governor import QuotaScope

    valid = {s.value: s for s in QuotaScope}
    quota_scope = valid.get(scope)
    if quota_scope is None:
        raise HTTPException(status_code=422, detail=f"invalid scope: {scope}")
    governor = _component(request, "cost_governor")
    status = await governor.get_status(principal.tenant_id, quota_scope, owner_id)
    return asdict(status)


@router.put("/quotas", tags=["admin"])
async def update_quota(
    _: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    raise HTTPException(
        status_code=501,
        detail="quota update not supported by CostGovernor protocol",
    )


# -- Approvals ---------------------------------------------------------------


@router.get("/approvals", tags=["admin"])
async def list_approvals(
    request: Request,
    status: str | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> ApprovalListResponse:
    gate = _component(request, "approval_gate")
    items = await gate.list_all(principal.tenant_id, status=status, limit=limit, offset=offset)
    total = await gate.count(principal.tenant_id, status=status)
    return ApprovalListResponse(
        items=[asdict(a) for a in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/approvals/{approval_id}/approve", tags=["admin"])
async def approve_request(
    approval_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    from eaos.core.errors import NotFoundError, PermissionDeniedError

    gate = _component(request, "approval_gate")
    try:
        await gate.approve(approval_id, principal.user_id, principal.tenant_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"id": str(approval_id), "status": "approved"}


@router.post("/approvals/{approval_id}/reject", tags=["admin"])
async def reject_request(
    approval_id: UUID,
    body: ApprovalActionRequest,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, str]:
    gate = _component(request, "approval_gate")
    await gate.reject(approval_id, principal.user_id, body.reason or "", principal.tenant_id)
    return {"id": str(approval_id), "status": "rejected"}


# -- Spans -------------------------------------------------------------------


@router.get("/spans/overview", tags=["admin"])
async def spans_overview(
    request: Request,
    start: datetime = Query(...),  # noqa: B008
    end: datetime = Query(...),  # noqa: B008
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> dict[str, Any]:
    from eaos.observability.query import DateRange

    trace_query = _component(request, "trace_query")
    date_range = DateRange(start=start, end=end)
    overview = await trace_query.overview(principal.tenant_id, date_range)
    return asdict(overview)


@router.get("/spans/trace/{trace_id}", tags=["admin"])
async def trace_detail(
    trace_id: UUID,
    request: Request,
    principal: Principal = Depends(require_admin),  # noqa: B008
) -> list[dict[str, Any]]:
    trace_query = _component(request, "trace_query")
    spans = await trace_query.trace_detail(trace_id)
    return [asdict(s) for s in spans]
