"""Assignment-owned safe summary reads, exported by the service interface."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, true
from sqlalchemy.dialects.postgresql import distinct_on

from app.core.errors import InvalidCursorError
from app.core.pagination import CursorParams, decode_cursor, encode_cursor
from app.modules.assignments.models import AssignmentGrade, AssignmentSubmission, SubmissionAttempt
from app.modules.identity.authz import Permission, require_org, require_org_permission

if TYPE_CHECKING:
    from app.modules.identity.dependencies import RequestContext


async def dashboard_attention(ctx: RequestContext) -> dict[str, Any]:
    org = require_org_permission(ctx.principal, Permission.ASSIGNMENT_GRADE)
    count, oldest = (
        await ctx.session.execute(
            select(func.count(), func.min(AssignmentSubmission.submitted_at)).where(
                AssignmentSubmission.organization_id == org,
                AssignmentSubmission.status == "submitted",
                AssignmentSubmission.user_id != ctx.principal.user_id,
                func.app.course_readable(AssignmentSubmission.course_id),
            )
        )
    ).one()
    return {"ungraded_count": count, "oldest_ungraded_at": oldest}


async def dashboard_queue(
    ctx: RequestContext, params: CursorParams
) -> tuple[list[AssignmentSubmission], str | None]:
    org = require_org_permission(ctx.principal, Permission.ASSIGNMENT_GRADE)
    s = AssignmentSubmission
    rank = case((s.status == "submitted", 0), else_=1)
    stmt = select(s).where(
        s.organization_id == org,
        s.user_id != ctx.principal.user_id,
        func.app.course_readable(s.course_id),
    )
    if params.cursor:
        try:
            data = decode_cursor(params.cursor)
            r, at, last = int(data["r"]), datetime.fromisoformat(data["t"]), UUID(data["id"])
            if r not in (0, 1) or at.tzinfo is None:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidCursorError from exc
        stmt = stmt.where(
            or_(
                rank > r,
                and_(rank == r, or_(s.submitted_at > at, and_(s.submitted_at == at, s.id > last))),
            )
        )
    rows = list(
        await ctx.session.scalars(stmt.order_by(rank, s.submitted_at, s.id).limit(params.limit + 1))
    )
    page = rows[: params.limit]
    cursor = None
    if len(rows) > params.limit:
        last_row = page[-1]
        cursor = encode_cursor(
            {
                "r": 0 if last_row.status == "submitted" else 1,
                "t": last_row.submitted_at.isoformat(),
                "id": str(last_row.id),
            }
        )
    return page, cursor


async def dashboard_own_results(
    ctx: RequestContext,
    after: tuple[datetime, str, UUID] | None,
    limit: int,
    enrollment_ids: list[UUID],
) -> list[dict[str, Any]]:
    org = require_org(ctx.principal)
    g, s, a = AssignmentGrade, AssignmentSubmission, SubmissionAttempt
    latest = (
        select(g.id, g.submission_id)
        .ext(distinct_on(g.submission_id))
        .order_by(g.submission_id, g.grade_sequence.desc())
        .subquery()
    )
    stmt = (
        select(g, s, a)
        .join(latest, latest.c.id == g.id)
        .join(s, s.id == g.submission_id)
        .join(a, a.id == s.active_attempt_id)
        .where(
            s.organization_id == org,
            s.user_id == ctx.principal.user_id,
            s.enrollment_id.in_(enrollment_ids),
            g.attempt_id == s.active_attempt_id,
            func.app.student_course_assigned(s.user_id, s.organization_id, s.course_id),
        )
    )
    if after:
        at, kind, last = after
        stmt = stmt.where(
            or_(
                g.graded_at < at,
                and_(g.graded_at == at, g.id < last if kind == "assignment" else true()),
            )
        )
    rows = await ctx.session.execute(stmt.order_by(g.graded_at.desc(), g.id.desc()).limit(limit))
    return [
        {
            "id": grade.id,
            "kind": "assignment",
            "enrollment_id": sub.enrollment_id,
            "course_id": sub.course_id,
            "lesson_id": sub.lesson_id,
            "title": attempt.assignment_rules["title"],
            "score": grade.score,
            "max_marks": grade.max_marks,
            "passed": None,
            "occurred_at": grade.graded_at,
        }
        for grade, sub, attempt in rows
    ]


async def dashboard_submitted_pairs(
    ctx: RequestContext, enrollment_ids: list[UUID]
) -> set[tuple[UUID, UUID]]:
    org = require_org(ctx.principal)
    rows = await ctx.session.execute(
        select(AssignmentSubmission.enrollment_id, AssignmentSubmission.lesson_id).where(
            AssignmentSubmission.organization_id == org,
            AssignmentSubmission.user_id == ctx.principal.user_id,
            AssignmentSubmission.enrollment_id.in_(enrollment_ids),
        )
    )
    return {(eid, lid) for eid, lid in rows}
