"""Enrollments HTTP API: the student's courses, the course player's data, lesson completion, and
org_admin opt-in to a new major version."""

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.core.pagination import CursorPage, PageParams
from app.core.redis import RedisClient
from app.modules.enrollments import service
from app.modules.enrollments.schemas import (
    EnrollmentDetail,
    EnrollmentOut,
    LessonCompletionOut,
    LessonImagesOut,
    UpgradeAccepted,
    UpgradeRequest,
    VideoHeartbeat,
    VideoResume,
)
from app.modules.identity.dependencies import RequestCtx
from app.modules.media.service import FileDownloadOut, PlaybackOut

router = APIRouter(tags=["enrollments"])


@router.post("/progress/heartbeat", status_code=204, operation_id="video_heartbeat")
async def video_heartbeat(
    ctx: RequestCtx, request: Request, redis: RedisClient, body: VideoHeartbeat
) -> None:
    await service.video_heartbeat(
        ctx, redis, body, interval_seconds=request.app.state.settings.heartbeat_interval_seconds
    )


@router.get(
    "/enrollments/{enrollment_id}/lessons/{lesson_id}/playback", operation_id="video_playback"
)
async def video_playback(
    ctx: RequestCtx,
    request: Request,
    enrollment_id: UUID,
    lesson_id: UUID,
) -> PlaybackOut:
    return await service.video_playback(
        ctx,
        request.app.state.video_providers,
        enrollment_id,
        lesson_id,
        request.app.state.settings.video_playback_ttl_seconds,
    )


@router.get("/enrollments/{enrollment_id}/lessons/{lesson_id}/resume", operation_id="video_resume")
async def video_resume(
    ctx: RequestCtx, redis: RedisClient, enrollment_id: UUID, lesson_id: UUID
) -> VideoResume:
    return await service.video_resume(ctx, redis, enrollment_id, lesson_id)


@router.post("/enrollments/{enrollment_id}/lessons/{lesson_id}/pdf-access", operation_id="open_pdf")
async def open_pdf(
    ctx: RequestCtx, request: Request, enrollment_id: UUID, lesson_id: UUID
) -> FileDownloadOut:
    """A signed URL for a pdf lesson's file (valid a few minutes; ask again when it expires).
    Records that the student opened the PDF, which completing the lesson requires."""
    return await service.open_pdf(
        ctx,
        request.app.state.storage,
        enrollment_id,
        lesson_id,
        request.app.state.settings.file_download_ttl_seconds,
    )


@router.get("/enrollments/{enrollment_id}/lessons/{lesson_id}/images", operation_id="notes_images")
async def notes_images(
    ctx: RequestCtx, request: Request, enrollment_id: UUID, lesson_id: UUID
) -> LessonImagesOut:
    """Signed URLs for a notes lesson's images (`<img data-file-id>` in its HTML)."""
    return await service.lesson_images(
        ctx,
        request.app.state.storage,
        enrollment_id,
        lesson_id,
        request.app.state.settings.file_download_ttl_seconds,
    )


@router.get("/enrollments", operation_id="list_my_enrollments")
async def list_my_enrollments(ctx: RequestCtx, page: PageParams) -> CursorPage[EnrollmentOut]:
    """The signed-in student's active enrollments in the active organization."""
    items, cursor = await service.list_my_enrollments(ctx, page)
    return CursorPage(items=items, next_cursor=cursor)


@router.get("/enrollments/{enrollment_id}", operation_id="get_enrollment")
async def get_enrollment(ctx: RequestCtx, enrollment_id: UUID) -> EnrollmentDetail:
    """The course outline the student sees (their major version's latest minor) and progress."""
    return await service.get_enrollment(ctx, enrollment_id)


@router.post("/enrollments/{enrollment_id}/lessons/{lesson_id}/visit", operation_id="visit_lesson")
async def visit_lesson(ctx: RequestCtx, enrollment_id: UUID, lesson_id: UUID) -> EnrollmentOut:
    """Record that the student opened a lesson (resume and "Continue learning")."""
    return await service.visit_lesson(ctx, enrollment_id, lesson_id)


@router.post(
    "/enrollments/{enrollment_id}/lessons/{lesson_id}/complete", operation_id="complete_lesson"
)
async def complete_lesson(
    ctx: RequestCtx, enrollment_id: UUID, lesson_id: UUID
) -> LessonCompletionOut:
    """Mark a notes lesson (or an opened PDF) complete; returns the new course progress."""
    return await service.complete_lesson(ctx, enrollment_id, lesson_id)


@router.post(
    "/courses/{course_id}/enrollment-upgrades",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="upgrade_enrollments",
)
async def upgrade_enrollments(
    ctx: RequestCtx, course_id: UUID, body: UpgradeRequest
) -> UpgradeAccepted:
    """Opt the organization's existing enrollments (or chosen batches') into a newer major
    version. Progress carries over for lessons that still exist. Org admins only."""
    return await service.request_upgrade(ctx, course_id, body)
