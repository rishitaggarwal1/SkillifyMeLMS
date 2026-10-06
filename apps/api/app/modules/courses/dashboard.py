"""Course-owned read projections for role landings; no learning-table queries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import aliased

from app.core.pagination import CursorParams, paginate_by_id
from app.modules.courses.models import Course, CourseAssignment, CourseVersion, Lesson
from app.modules.identity.authz import Permission, require_org_permission

if TYPE_CHECKING:
    from app.modules.identity.dependencies import RequestContext


async def dashboard_author_checklist(ctx: RequestContext) -> dict[str, bool]:
    org = require_org_permission(ctx.principal, Permission.COURSE_READ)
    owned = (Course.organization_id == org, Course.status == "active")
    row = (
        await ctx.session.execute(
            select(
                select(Course.id).where(*owned).exists(),
                select(Lesson.id)
                .join(Course, Course.id == Lesson.course_id)
                .where(*owned)
                .exists(),
                select(Course.id).where(*owned, Course.current_version_id.is_not(None)).exists(),
            )
        )
    ).one()
    return dict(zip(("has_course", "has_lesson", "has_publication"), row, strict=True))


async def dashboard_has_batch_assignment(ctx: RequestContext) -> bool:
    org = require_org_permission(ctx.principal, Permission.COURSE_DISTRIBUTE)
    return bool(
        await ctx.session.scalar(
            select(
                select(CourseAssignment.id)
                .where(
                    CourseAssignment.organization_id == org, CourseAssignment.batch_id.is_not(None)
                )
                .exists()
            )
        )
    )


async def dashboard_unassigned_grants(
    ctx: RequestContext, page: CursorParams
) -> tuple[list[Course], str | None]:
    org = require_org_permission(ctx.principal, Permission.COURSE_DISTRIBUTE)
    batch = aliased(CourseAssignment)
    stmt = select(Course).where(
        Course.status == "active",
        select(CourseAssignment.id)
        .where(
            CourseAssignment.course_id == Course.id,
            CourseAssignment.organization_id == org,
            CourseAssignment.batch_id.is_(None),
        )
        .exists(),
        ~select(batch.id)
        .where(
            batch.course_id == Course.id, batch.organization_id == org, batch.batch_id.is_not(None)
        )
        .exists(),
    )
    return await paginate_by_id(ctx.session, stmt, Course.id, page)


async def dashboard_due_assignments(
    ctx: RequestContext, now: datetime, refs: Sequence[tuple[UUID, int]] | None = None
) -> list[dict[str, Any]]:
    """Latest published minor per displayed major; only safe assignment metadata.

    Private service result, not an HTTP list. reports applies a cursor to the
    flattened items. This follows the existing OLTP report stopgap until Phase 5.
    """
    latest = (
        select(
            CourseVersion.course_id,
            CourseVersion.major,
            func.max(CourseVersion.minor).label("minor"),
        )
        .group_by(CourseVersion.course_id, CourseVersion.major)
        .subquery()
    )
    stmt = (
        select(CourseVersion)
        .join(
            latest,
            (CourseVersion.course_id == latest.c.course_id)
            & (CourseVersion.major == latest.c.major)
            & (CourseVersion.minor == latest.c.minor),
        )
        .join(Course, Course.id == CourseVersion.course_id)
        .where(Course.status == "active")
    )
    if refs is None:
        require_org_permission(ctx.principal, Permission.COURSE_READ)
        stmt = stmt.where(CourseVersion.id == Course.current_version_id)
    else:
        stmt = stmt.where(tuple_(CourseVersion.course_id, CourseVersion.major).in_(refs))
    versions = list(await ctx.session.scalars(stmt))
    result = []
    for version in versions:
        for module in version.snapshot.get("modules", []):
            for lesson in module.get("lessons", []):
                content = lesson.get("content", {})
                if lesson["lesson_type"] != "assignment" or not content.get("due_at"):
                    continue
                due = datetime.fromisoformat(content["due_at"])
                if now <= due and (due - now).total_seconds() <= 7 * 86400:
                    result.append(
                        {
                            "course_id": version.course_id,
                            "major": version.major,
                            "lesson_id": UUID(lesson["id"]),
                            "title": content.get("title", lesson["title"]),
                            "course_title": version.title,
                            "due_at": due,
                        }
                    )
    return result


async def dashboard_batch_courses(
    ctx: RequestContext, batch_ids: Sequence[UUID]
) -> dict[UUID, list[UUID]]:
    org = require_org_permission(ctx.principal, Permission.BATCH_MANAGE)
    rows = await ctx.session.execute(
        select(CourseAssignment.batch_id, CourseAssignment.course_id).where(
            CourseAssignment.organization_id == org, CourseAssignment.batch_id.in_(batch_ids)
        )
    )
    result: dict[UUID, list[UUID]] = {bid: [] for bid in batch_ids}
    for bid, cid in rows:
        if bid is not None:
            result[bid].append(cid)
    return result
