"""Progress reports HTTP API (staff of the active org; read-only)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.core.pagination import CursorPage, PageParams
from app.modules.assignments import service as assignments
from app.modules.assignments.schemas import CrossCourseSubmissionRow
from app.modules.courses import service as courses
from app.modules.courses.schemas import CourseOut
from app.modules.identity.dependencies import RequestCtx
from app.modules.reports import dashboard, service
from app.modules.reports.schemas import (
    AdminOverview,
    BatchCourseSummary,
    CourseProgressPage,
    DashboardBatch,
    DueAssignment,
    LearningResult,
    TeachOverview,
)

router = APIRouter(tags=["reports"])

BatchQuery = Annotated[UUID, Query(description="One of the org's batches assigned the course")]


@router.get("/dashboards/admin", operation_id="admin_overview")
async def admin_overview(ctx: RequestCtx) -> AdminOverview:
    return await dashboard.admin_overview(ctx)


@router.get("/dashboards/admin/batches", operation_id="dashboard_batches")
async def dashboard_batches(ctx: RequestCtx, page: PageParams) -> CursorPage[DashboardBatch]:
    items, cursor = await dashboard.admin_batches(ctx, page)
    return CursorPage(items=items, next_cursor=cursor)


@router.get("/dashboards/admin/unassigned-courses", operation_id="unassigned_granted_courses")
async def unassigned_granted_courses(ctx: RequestCtx, page: PageParams) -> CursorPage[CourseOut]:
    items, cursor = await courses.dashboard_unassigned_courses(ctx, page)
    return CursorPage(items=items, next_cursor=cursor)


@router.get("/dashboards/teach", operation_id="teach_overview")
async def teach_overview(ctx: RequestCtx) -> TeachOverview:
    return await dashboard.teach_overview(ctx)


@router.get("/assignment-submissions", operation_id="cross_course_grading_queue")
async def cross_course_grading_queue(
    ctx: RequestCtx, page: PageParams
) -> CursorPage[CrossCourseSubmissionRow]:
    items, cursor = await assignments.cross_course_queue(ctx, page)
    return CursorPage(items=items, next_cursor=cursor)


@router.get("/dashboards/learn/due", operation_id="learning_due_soon")
async def learning_due_soon(ctx: RequestCtx, page: PageParams) -> CursorPage[DueAssignment]:
    items, cursor = await dashboard.learning_due(ctx, page)
    return CursorPage(items=items, next_cursor=cursor)


@router.get("/dashboards/learn/results", operation_id="recent_learning_results")
async def recent_learning_results(ctx: RequestCtx, page: PageParams) -> CursorPage[LearningResult]:
    items, cursor = await dashboard.learning_results(ctx, page)
    return CursorPage(items=items, next_cursor=cursor)


@router.get("/courses/{course_id}/progress", operation_id="course_progress")
async def course_progress(
    ctx: RequestCtx, course_id: UUID, batch_id: BatchQuery, page: PageParams
) -> CourseProgressPage:
    """One page of a batch's students (by name) with progress %, last activity, per-lesson
    completion and assignment status. Columns are the lessons of the latest version."""
    return await service.course_progress(ctx, course_id, batch_id, page)


@router.get(
    "/courses/{course_id}/progress.csv",
    operation_id="course_progress_csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "The whole batch as CSV"}},
)
async def course_progress_csv(ctx: RequestCtx, course_id: UUID, batch_id: BatchQuery) -> Response:
    file_name, content = await service.course_progress_csv(ctx, course_id, batch_id)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get("/batches/{batch_id}/courses", operation_id="batch_courses")
async def batch_courses(
    ctx: RequestCtx, batch_id: UUID, page: PageParams
) -> CursorPage[BatchCourseSummary]:
    """Courses assigned to the batch, with how far its students are in each."""
    items, cursor = await service.batch_courses(ctx, batch_id, page)
    return CursorPage(items=items, next_cursor=cursor)
