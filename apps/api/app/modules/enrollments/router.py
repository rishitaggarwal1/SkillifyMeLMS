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
    UpgradeAccepted,
    UpgradeRequest,
    VideoHeartbeat,
    VideoResume,
)
from app.modules.identity.dependencies import RequestCtx
from app.modules.media.service import PlaybackOut

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
