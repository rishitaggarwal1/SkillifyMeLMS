"""Enrollment-owned dashboard reads. Live batch entitlement is a courses SQL interface."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select

from app.modules.enrollments.models import Enrollment
from app.modules.identity.authz import Permission, require_org, require_org_permission

if TYPE_CHECKING:
    from app.modules.identity.dependencies import RequestContext


def _live() -> Any:
    return func.app.student_course_assigned(
        Enrollment.user_id, Enrollment.organization_id, Enrollment.course_id
    )


async def dashboard_own_refs(ctx: RequestContext) -> list[tuple[UUID, UUID, int]]:
    org = require_org(ctx.principal)
    rows = await ctx.session.execute(
        select(Enrollment.id, Enrollment.course_id, Enrollment.major_version).where(
            Enrollment.organization_id == org,
            Enrollment.user_id == ctx.principal.user_id,
            Enrollment.status == "active",
            _live(),
        )
    )
    return [(eid, cid, major) for eid, cid, major in rows]


async def dashboard_activity(ctx: RequestContext, cutoff: datetime) -> int:
    org = require_org_permission(ctx.principal, Permission.COURSE_READ)
    inactive = (
        select(Enrollment.user_id)
        .where(Enrollment.organization_id == org, Enrollment.status == "active", _live())
        .group_by(Enrollment.user_id)
        .having(
            func.max(func.coalesce(Enrollment.last_accessed_at, Enrollment.created_at)) <= cutoff,
            func.bool_or(Enrollment.completed_at.is_(None)),
        )
        .subquery()
    )
    return int(await ctx.session.scalar(select(func.count()).select_from(inactive)) or 0)


async def dashboard_batch_activity(
    ctx: RequestContext, members: dict[UUID, list[UUID]], assigned: dict[UUID, list[UUID]]
) -> dict[UUID, dict[str, Any]]:
    org = require_org_permission(ctx.principal, Permission.BATCH_MANAGE)
    users: Sequence[UUID] = list({u for ids in members.values() for u in ids})
    rows = list(
        await ctx.session.execute(
            select(
                Enrollment.user_id,
                Enrollment.course_id,
                Enrollment.progress_percent,
                Enrollment.last_accessed_at,
            ).where(
                Enrollment.organization_id == org,
                Enrollment.user_id.in_(users),
                Enrollment.status == "active",
                _live(),
            )
        )
    )
    by_pair = {(u, cid): (percent, at) for u, cid, percent, at in rows}
    result = {}
    for bid, ids in members.items():
        parts = [by_pair[(u, cid)] for u in ids for cid in assigned[bid] if (u, cid) in by_pair]
        dates = [p[1] for p in parts if p[1]]
        result[bid] = {
            "completion_percent": round(sum(p[0] for p in parts) / len(parts)) if parts else None,
            "last_activity_at": max(dates) if dates else None,
            "enrolled": len(parts),
        }
    return result
