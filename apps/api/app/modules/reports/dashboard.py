"""Role summaries composed exclusively from owning modules' service interfaces.

Like the existing progress reports, this is an OLTP stopgap until Phase 5.
HTTP lists are bounded cursors; query count is independent of row count.
"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text

from app.core.errors import InvalidCursorError
from app.core.pagination import CursorParams, decode_cursor, encode_cursor
from app.modules.assessments import service as assessments
from app.modules.assignments import service as assignments
from app.modules.courses import service as courses
from app.modules.enrollments import service as enrollments
from app.modules.identity import service as identity
from app.modules.identity.authz import require_role
from app.modules.identity.dependencies import RequestContext
from app.modules.identity.models import OrgRole
from app.modules.reports.schemas import (
    AdminOverview,
    DashboardBatch,
    DueAssignment,
    LearningResult,
    TeachOverview,
)


async def _now(ctx: RequestContext) -> datetime:
    value = await ctx.session.scalar(text("SELECT current_timestamp"))
    assert isinstance(value, datetime)  # noqa: S101
    return value


async def admin_overview(ctx: RequestContext) -> AdminOverview:
    state = await identity.dashboard_overview(ctx)
    return AdminOverview(**state, has_assignment=await courses.dashboard_has_batch_assignment(ctx))


async def admin_batches(
    ctx: RequestContext, page: CursorParams
) -> tuple[list[DashboardBatch], str | None]:
    batches, cursor = await identity.dashboard_batches(ctx, page)
    members = await identity.dashboard_batch_students(ctx, [b.id for b in batches])
    assigned = await courses.dashboard_batch_courses(ctx, [b.id for b in batches])
    stats = await enrollments.dashboard_batch_activity(ctx, members, assigned)
    return [DashboardBatch(id=b.id, name=b.name, **stats[b.id]) for b in batches], cursor


async def teach_overview(ctx: RequestContext) -> TeachOverview:
    now = await _now(ctx)
    checklist = await courses.dashboard_author_checklist(ctx)
    attention = await assignments.dashboard_attention(ctx)
    outcomes = await assessments.dashboard_outcomes(ctx)
    due = await courses.dashboard_due_assignments(ctx, now)
    return TeachOverview.model_validate(
        {
            **checklist,
            **attention,
            **outcomes,
            "assignments_due_soon": len(due),
            "inactive_students": await enrollments.dashboard_activity(ctx, now - timedelta(days=7)),
        }
    )


async def learning_due(
    ctx: RequestContext, page: CursorParams
) -> tuple[list[DueAssignment], str | None]:
    require_role(ctx.principal, OrgRole.STUDENT)
    now = await _now(ctx)
    refs = await enrollments.dashboard_own_refs(ctx)
    due = await courses.dashboard_due_assignments(
        ctx, now, [(cid, major) for _, cid, major in refs]
    )
    eid_of = {(cid, major): eid for eid, cid, major in refs}
    submitted = await assignments.dashboard_submitted_pairs(ctx, [eid for eid, _, _ in refs])
    items = [
        DueAssignment(
            enrollment_id=eid_of[(d["course_id"], d["major"])],
            **{k: v for k, v in d.items() if k != "major"},
        )
        for d in due
        if (eid_of[(d["course_id"], d["major"])], d["lesson_id"]) not in submitted
    ]
    items.sort(key=lambda d: (d.due_at, d.enrollment_id, d.lesson_id))
    if page.cursor:
        try:
            c = decode_cursor(page.cursor)
            after = (datetime.fromisoformat(c["t"]), UUID(c["e"]), UUID(c["l"]))
            if after[0].tzinfo is None:
                raise ValueError
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidCursorError from exc
        items = [d for d in items if (d.due_at, d.enrollment_id, d.lesson_id) > after]
    rows = items[: page.limit]
    cursor = None
    if len(items) > page.limit:
        last = rows[-1]
        cursor = encode_cursor(
            {"t": last.due_at.isoformat(), "e": str(last.enrollment_id), "l": str(last.lesson_id)}
        )
    return rows, cursor


async def learning_results(
    ctx: RequestContext, page: CursorParams
) -> tuple[list[LearningResult], str | None]:
    require_role(ctx.principal, OrgRole.STUDENT)
    after = None
    if page.cursor:
        try:
            c = decode_cursor(page.cursor)
            after = (datetime.fromisoformat(c["t"]), str(c["k"]), UUID(c["id"]))
            if after[0].tzinfo is None or after[1] not in ("assignment", "quiz"):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidCursorError from exc
    refs = await enrollments.dashboard_own_refs(ctx)
    grades = await assignments.dashboard_own_results(
        ctx, after, page.limit + 1, [eid for eid, _, _ in refs]
    )
    quizzes = await assessments.dashboard_own_results(ctx, after, page.limit + 1)
    candidates = sorted(
        grades + quizzes, key=lambda d: (d["occurred_at"], d["kind"], d["id"]), reverse=True
    )
    rows = candidates[: page.limit]
    titles = await courses.course_titles(ctx.session, [r["course_id"] for r in rows])
    items = [LearningResult(**r, course_title=titles.get(r["course_id"], "Course")) for r in rows]
    cursor = None
    if len(candidates) > page.limit:
        last = items[-1]
        cursor = encode_cursor(
            {"t": last.occurred_at.isoformat(), "k": last.kind, "id": str(last.id)}
        )
    return items, cursor
