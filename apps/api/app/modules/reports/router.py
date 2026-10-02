"""Progress reports HTTP API (staff of the active org; read-only)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.core.pagination import CursorPage, PageParams
from app.modules.identity.dependencies import RequestCtx
from app.modules.reports import service
from app.modules.reports.schemas import BatchCourseSummary, CourseProgressPage

router = APIRouter(tags=["reports"])

BatchQuery = Annotated[UUID, Query(description="One of the org's batches assigned the course")]


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
