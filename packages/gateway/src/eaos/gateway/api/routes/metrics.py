"""Admin metrics API — dashboard summary counts and trends.

- ``GET /admin/metrics`` — aggregate counts across the tenant's tables:
  users, agents, sessions, skills, documents, pending approvals, notifications.
  Also includes session activity trend (configurable time range + granularity).
- ``GET /admin/metrics/export`` — CSV export of metrics data.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta  # noqa: TC003
from typing import TYPE_CHECKING, Any

from eaos.core.auth import Principal  # noqa: TC002
from eaos.gateway.api.deps import get_db
from eaos.gateway.api.routes.admin import require_admin
from fastapi import APIRouter, Depends, Query  # noqa: TC002
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from eaos.infra.db.base import DbClient

router = APIRouter(prefix="/admin/metrics", tags=["metrics"])

_GRANULARITY_INTERVAL = {"hour": "1 hour", "day": "1 day", "week": "1 week"}


@router.get("", status_code=200)
async def get_metrics(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    start_time: datetime | None = Query(default=None),  # noqa: B008
    end_time: datetime | None = Query(default=None),  # noqa: B008
    granularity: str = Query(default="day"),  # noqa: B008
) -> dict[str, Any]:
    """Return aggregated dashboard metrics for the tenant.

    Activity trend supports optional ``start_time`` / ``end_time`` (defaults to
    last 7 days) and ``granularity`` (hour / day / week, default day).
    """
    user_count = await db.fetch_val(
        "SELECT COUNT(*) FROM iam.users WHERE tenant_id = :p0",
        principal.tenant_id,
    )
    agent_count = await db.fetch_val(
        "SELECT COUNT(*) FROM agent.agents WHERE tenant_id = :p0",
        principal.tenant_id,
    )
    session_count = await db.fetch_val(
        "SELECT COUNT(*) FROM agent.sessions WHERE tenant_id = :p0",
        principal.tenant_id,
    )
    skill_count = await db.fetch_val(
        "SELECT COUNT(*) FROM skills.skills WHERE tenant_id = :p0",
        principal.tenant_id,
    )
    doc_count = await db.fetch_val(
        "SELECT COUNT(*) FROM knowledge.documents WHERE tenant_id = :p0",
        principal.tenant_id,
    )
    pending_approvals = await db.fetch_val(
        "SELECT COUNT(*) FROM harness.approvals "
        "WHERE tenant_id = :p0 AND status = 'pending'",
        principal.tenant_id,
    )
    unread_notifications = await db.fetch_val(
        "SELECT COUNT(*) FROM iam.notifications "
        "WHERE tenant_id = :p0 AND user_id = :p1 AND read_at IS NULL",
        principal.tenant_id,
        principal.user_id,
    )

    end = end_time or datetime.utcnow()
    start = start_time or (end - timedelta(days=7))
    interval = _GRANULARITY_INTERVAL.get(granularity, "1 day")

    activity = await db.fetch(
        f"SELECT DATE_TRUNC('{granularity}', created_at) AS bucket, "
        f"COUNT(*) AS count "
        f"FROM agent.sessions WHERE tenant_id = :p0 "
        f"AND created_at >= :p1 AND created_at <= :p2 "
        f"GROUP BY bucket ORDER BY bucket",
        principal.tenant_id,
        start,
        end,
    )

    return {
        "counts": {
            "users": int(user_count or 0),
            "agents": int(agent_count or 0),
            "sessions": int(session_count or 0),
            "skills": int(skill_count or 0),
            "documents": int(doc_count or 0),
            "pending_approvals": int(pending_approvals or 0),
            "unread_notifications": int(unread_notifications or 0),
        },
        "activity": [
            {"bucket": str(r["bucket"]), "sessions": int(r["count"])}
            for r in activity
        ],
        "time_range": {"start": start.isoformat(), "end": end.isoformat()},
        "granularity": granularity,
    }


@router.get("/export", status_code=200)
async def export_metrics(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
    start_time: datetime | None = Query(default=None),  # noqa: B008
    end_time: datetime | None = Query(default=None),  # noqa: B008
    granularity: str = Query(default="day"),  # noqa: B008
) -> StreamingResponse:
    """Export metrics data as CSV."""
    end = end_time or datetime.utcnow()
    start = start_time or (end - timedelta(days=7))

    user_count = await db.fetch_val(
        "SELECT COUNT(*) FROM iam.users WHERE tenant_id = :p0",
        principal.tenant_id,
    )
    agent_count = await db.fetch_val(
        "SELECT COUNT(*) FROM agent.agents WHERE tenant_id = :p0",
        principal.tenant_id,
    )
    session_count = await db.fetch_val(
        "SELECT COUNT(*) FROM agent.sessions WHERE tenant_id = :p0",
        principal.tenant_id,
    )
    skill_count = await db.fetch_val(
        "SELECT COUNT(*) FROM skills.skills WHERE tenant_id = :p0",
        principal.tenant_id,
    )
    doc_count = await db.fetch_val(
        "SELECT COUNT(*) FROM knowledge.documents WHERE tenant_id = :p0",
        principal.tenant_id,
    )
    pending_approvals = await db.fetch_val(
        "SELECT COUNT(*) FROM harness.approvals "
        "WHERE tenant_id = :p0 AND status = 'pending'",
        principal.tenant_id,
    )

    activity = await db.fetch(
        f"SELECT DATE_TRUNC('{granularity}', created_at) AS bucket, "
        f"COUNT(*) AS count "
        f"FROM agent.sessions WHERE tenant_id = :p0 "
        f"AND created_at >= :p1 AND created_at <= :p2 "
        f"GROUP BY bucket ORDER BY bucket",
        principal.tenant_id,
        start,
        end,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["metric", "value"])
    writer.writerow(["users", int(user_count or 0)])
    writer.writerow(["agents", int(agent_count or 0)])
    writer.writerow(["sessions", int(session_count or 0)])
    writer.writerow(["skills", int(skill_count or 0)])
    writer.writerow(["documents", int(doc_count or 0)])
    writer.writerow(["pending_approvals", int(pending_approvals or 0)])
    writer.writerow([])
    writer.writerow(["bucket", "sessions"])
    for row in activity or []:
        writer.writerow([row["bucket"], int(row["count"])])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=metrics.csv"},
    )
