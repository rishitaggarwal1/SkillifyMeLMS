"""Score-only landing projections. Never select answers, keys or explanations."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import and_, false, func, or_, select, text

from app.modules.assessments.models import QuizAttempt, QuizVersion
from app.modules.identity.authz import Permission, require_org, require_org_permission

if TYPE_CHECKING:
    from app.modules.identity.dependencies import RequestContext


async def dashboard_outcomes(ctx: RequestContext) -> dict[str, int]:
    require_org_permission(ctx.principal, Permission.COURSE_READ)
    # Keep the aggregate inside assessments. It checks active enrollment/batch
    # entitlement without exposing individual work or changing attempt/key grants.

    row = (
        await ctx.session.execute(text("SELECT passed, failed FROM app.quiz_dashboard_outcomes()"))
    ).one()
    return {"quiz_passes": row.passed, "quiz_failures": row.failed}


async def dashboard_own_results(
    ctx: RequestContext, after: tuple[datetime, str, UUID] | None, limit: int
) -> list[dict[str, Any]]:
    org = require_org(ctx.principal)
    a = QuizAttempt
    stmt = (
        select(
            a.id,
            a.enrollment_id,
            a.course_id,
            a.lesson_id,
            a.score,
            a.max_marks,
            a.passed,
            a.submitted_at,
            func.coalesce(QuizVersion.title, "Quiz result").label("title"),
        )
        .outerjoin(QuizVersion, QuizVersion.id == a.quiz_version_id)
        .where(a.organization_id == org, a.user_id == ctx.principal.user_id, a.state == "submitted")
    )
    if after:
        at, kind, last = after
        stmt = stmt.where(
            or_(
                a.submitted_at < at,
                and_(a.submitted_at == at, a.id < last if kind == "quiz" else false()),
            )
        )
    rows = await ctx.session.execute(stmt.order_by(a.submitted_at.desc(), a.id.desc()).limit(limit))
    return [
        {
            "id": row.id,
            "kind": "quiz",
            "enrollment_id": row.enrollment_id,
            "course_id": row.course_id,
            "lesson_id": row.lesson_id,
            "title": row.title,
            "score": row.score,
            "max_marks": row.max_marks,
            "passed": row.passed,
            "occurred_at": row.submitted_at,
        }
        for row in rows
    ]
