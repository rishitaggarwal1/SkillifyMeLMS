"""Staff progress reports: one course for one batch (table and CSV), and a batch's courses.

STOPGAP until Phase 5: read from the OLTP tables through the identity, courses, enrollments and
assignments service interfaces, one page of students at a time with a fixed number of queries
per page (no N+1; see test_progress_report). Phase 5 serves the same responses from ClickHouse.

Who: `org_admin` and `instructor` of the active org (`course.read`), for **their own org's
batches** that have a batch assignment for the course. RLS already limits staff to their org's
enrollments and submissions; the batch check gives a clear 404 for anything else.
"""

import csv
import io
from uuid import UUID

from app.core.csv_safety import csv_cell
from app.core.errors import InvalidCursorError, NotFoundError
from app.core.pagination import CursorParams, decode_cursor, encode_cursor
from app.modules.assignments import service as assignments
from app.modules.courses import service as courses
from app.modules.courses.service import VersionRef
from app.modules.enrollments import service as enrollments
from app.modules.identity import service as identity
from app.modules.identity.authz import Permission, require_org_permission
from app.modules.identity.dependencies import RequestContext
from app.modules.reports.schemas import (
    AssignmentCell,
    BatchCourseSummary,
    CourseProgressPage,
    LessonCell,
    ProgressLesson,
    StudentProgress,
    StudentRef,
)

Ctx = RequestContext
CSV_PAGE = 500


async def _assigned_batch(ctx: Ctx, org_id: UUID, course_id: UUID, batch_id: UUID) -> None:
    """404 unless the batch is the org's and the course is assigned to it."""
    if not await identity.batch_belongs_to_org(ctx.session, batch_id, org_id):
        raise NotFoundError("Batch not found.")
    if batch_id not in await courses.batch_assignments(ctx.session, course_id, org_id):
        raise NotFoundError("This course isn't assigned to that batch.")


async def _latest(ctx: Ctx, course_id: UUID) -> VersionRef | None:
    major = await courses.latest_major(ctx.session, course_id)
    if major is None:
        return None
    return await courses.resolve_version(ctx.session, course_id, major, redis=ctx.redis)


def _columns(version: VersionRef | None) -> list[ProgressLesson]:
    if version is None:
        return []
    return [
        ProgressLesson(
            id=UUID(lesson["id"]),
            title=lesson["title"],
            lesson_type=lesson["lesson_type"],
            module_title=module_title,
        )
        for module_title, lesson in courses.outline_lessons(version)
    ]


def _cell(in_version: bool, status: str | None) -> LessonCell:
    if not in_version:
        return "not_in_version"
    if status == "completed":
        return "completed"
    if status == "in_progress":
        return "in_progress"
    return "not_started"


async def _page(
    ctx: Ctx,
    course_id: UUID,
    batch_id: UUID,
    columns: list[ProgressLesson],
    *,
    after: tuple[str, UUID] | None,
    limit: int,
) -> tuple[list[StudentProgress], tuple[str, UUID] | None]:
    """One page of students (by name) with their progress: a fixed number of queries."""
    students = await identity.batch_students_page(
        ctx.session, batch_id, after=after, limit=limit + 1
    )
    page, more = students[:limit], len(students) > limit
    progress = await enrollments.course_progress_for_users(
        ctx.session, course_id, [s.id for s in page]
    )
    assignment_lessons = [c.id for c in columns if c.lesson_type == "assignment"]
    states = await assignments.submission_states(
        ctx.session, [p.enrollment_id for p in progress.values()], assignment_lessons
    )
    # Which column lessons exist in each pinned major's version (students may be on older ones).
    majors = sorted({p.major_version for p in progress.values()})
    versions = await courses.resolve_versions(
        ctx.session, [(course_id, m) for m in majors], redis=ctx.redis
    )
    lessons_of = await courses.version_lessons_many(
        ctx.session, [v.id for v in versions.values()], redis=ctx.redis
    )
    present = {
        major: {lesson.lesson_id for lesson in lessons_of[v.id]}
        for (_, major), v in versions.items()
    }

    rows: list[StudentProgress] = []
    for student in page:
        p = progress.get(student.id)
        ref = StudentRef(id=student.id, full_name=student.full_name, email=student.email)
        if p is None:
            rows.append(
                StudentProgress(
                    student=ref,
                    enrollment_id=None,
                    enrollment_status="not_enrolled",
                    version=None,
                    progress_percent=0,
                    last_activity_at=None,
                    completed_at=None,
                    lessons={},
                    assignments={},
                )
            )
            continue
        in_version = present.get(p.major_version, set())
        cells: dict[UUID, LessonCell] = {
            c.id: _cell(c.id in in_version, p.lessons.get(c.id)) for c in columns
        }
        shown = versions.get((course_id, p.major_version))
        rows.append(
            StudentProgress(
                student=ref,
                enrollment_id=p.enrollment_id,
                enrollment_status=p.status,  # type: ignore[arg-type]
                version=shown.label if shown else None,
                progress_percent=p.progress_percent,
                last_activity_at=p.last_accessed_at,
                completed_at=p.completed_at,
                lessons=cells,
                assignments={
                    lesson_id: AssignmentCell(
                        status=s.status,  # type: ignore[arg-type]
                        score=s.score,
                        max_marks=s.max_marks,
                    )
                    for (eid, lesson_id), s in states.items()
                    if eid == p.enrollment_id
                },
            )
        )
    last = page[-1] if page and more else None
    return rows, (last.full_name.lower(), last.id) if last else None


async def course_progress(
    ctx: Ctx, course_id: UUID, batch_id: UUID, params: CursorParams
) -> CourseProgressPage:
    org_id = require_org_permission(ctx.principal, Permission.COURSE_READ)
    course = await courses.readable_course(ctx, course_id)
    await _assigned_batch(ctx, org_id, course_id, batch_id)
    after: tuple[str, UUID] | None = None
    if params.cursor:
        data = decode_cursor(params.cursor)
        try:
            after = (str(data["n"]), UUID(str(data["id"])))
        except (KeyError, ValueError) as exc:
            raise InvalidCursorError from exc
    latest = await _latest(ctx, course_id)
    columns = _columns(latest)
    rows, next_after = await _page(
        ctx, course_id, batch_id, columns, after=after, limit=params.limit
    )
    return CourseProgressPage(
        course_id=course_id,
        course_title=course.title,
        batch_id=batch_id,
        version=latest.label if latest else None,
        lessons=columns,
        items=rows,
        next_cursor=encode_cursor({"n": next_after[0], "id": str(next_after[1])})
        if next_after
        else None,
    )


_CELL_TEXT: dict[str, str] = {
    "completed": "done",
    "in_progress": "started",
    "not_started": "",
    "not_in_version": "n/a",
}


def _assignment_text(cell: AssignmentCell | None) -> str:
    if cell is None:
        return ""
    if cell.status == "graded" and cell.score is not None:
        return f"{cell.score:.2f}/{cell.max_marks}"
    return "submitted"


async def course_progress_csv(ctx: Ctx, course_id: UUID, batch_id: UUID) -> tuple[str, str]:
    """The whole batch as CSV (fetched in pages of 500). Returns (file name, CSV text). Cells are
    neutralized against spreadsheet formula injection."""
    org_id = require_org_permission(ctx.principal, Permission.COURSE_READ)
    course = await courses.readable_course(ctx, course_id)
    await _assigned_batch(ctx, org_id, course_id, batch_id)
    columns = _columns(await _latest(ctx, course_id))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "Name",
            "Email",
            "Enrollment",
            "Version",
            "Progress %",
            "Last activity (UTC)",
            "Completed (UTC)",
            *(csv_cell(f"{c.module_title} / {c.title}") for c in columns),
        ]
    )
    after: tuple[str, UUID] | None = None
    while True:
        rows, after = await _page(ctx, course_id, batch_id, columns, after=after, limit=CSV_PAGE)
        for r in rows:
            cells = [
                _assignment_text(r.assignments.get(c.id))
                if c.lesson_type == "assignment" and c.id in r.assignments
                else _CELL_TEXT.get(r.lessons.get(c.id, "not_started"), "")
                for c in columns
            ]
            writer.writerow(
                [
                    csv_cell(r.student.full_name),
                    csv_cell(r.student.email),
                    r.enrollment_status,
                    r.version or "",
                    r.progress_percent,
                    r.last_activity_at.isoformat() if r.last_activity_at else "",
                    r.completed_at.isoformat() if r.completed_at else "",
                    *cells,
                ]
            )
        if after is None:
            break
    safe_title = "".join(ch if ch.isalnum() else "-" for ch in course.title.lower()).strip("-")
    return f"progress-{safe_title[:60] or 'course'}.csv", buffer.getvalue()


async def batch_courses(
    ctx: Ctx, batch_id: UUID, params: CursorParams
) -> tuple[list[BatchCourseSummary], str | None]:
    """Courses assigned to one of the org's batches, with the batch's progress in each."""
    org_id = require_org_permission(ctx.principal, Permission.COURSE_READ)
    if not await identity.batch_belongs_to_org(ctx.session, batch_id, org_id):
        raise NotFoundError("Batch not found.")
    course_ids = sorted(await courses.courses_for_batches(ctx.session, org_id, [batch_id]))
    if params.cursor:
        try:
            after = UUID(str(decode_cursor(params.cursor)["id"]))
        except (KeyError, ValueError) as exc:
            raise InvalidCursorError from exc
        course_ids = [c for c in course_ids if c > after]
    page, more = course_ids[: params.limit], len(course_ids) > params.limit
    titles = await courses.course_titles(ctx.session, page)
    majors = await courses.latest_majors(ctx.session, page)
    versions = await courses.resolve_versions(ctx.session, list(majors.items()), redis=ctx.redis)
    students = await identity.batch_student_ids(ctx.session, batch_id)
    summaries = await enrollments.course_summaries_for_users(ctx.session, page, students)
    items = []
    for course_id in page:
        enrolled, completed, average = summaries.get(course_id, (0, 0, 0.0))
        version = versions.get((course_id, majors[course_id])) if course_id in majors else None
        items.append(
            BatchCourseSummary(
                course_id=course_id,
                title=titles.get(course_id, ""),
                version=version.label if version else None,
                enrolled=enrolled,
                completed=completed,
                average_percent=int(average),
            )
        )
    return items, encode_cursor({"id": str(page[-1])}) if more and page else None
