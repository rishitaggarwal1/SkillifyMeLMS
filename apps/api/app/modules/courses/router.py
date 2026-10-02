"""Courses HTTP API: builder, publishing, versions and assignments.

Every edit of the outline or lessons (modules, lessons, reorders, skill tags) requires an
`If-Match: <revision>` header: the course revision the client started from. A missing header gets
`428 precondition_required` and a stale one `409 revision_conflict`, so concurrent editors never
silently overwrite each other. `PATCH /courses/{id}` (course details, not the outline) accepts it
optionally.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status

from app.core.errors import PreconditionRequiredError, UnprocessableError
from app.core.pagination import CursorPage, PageParams
from app.db.session import DbSession
from app.modules.courses import service
from app.modules.courses.schemas import (
    AssignmentCreate,
    AssignmentOut,
    CatalogEntryOut,
    CourseCreate,
    CourseOut,
    CourseUpdate,
    DraftOut,
    ImageUrlsOut,
    LessonCreate,
    LessonOut,
    LessonSkillsUpdate,
    LessonUpdate,
    ModuleCreate,
    ModuleOut,
    ModuleUpdate,
    NotesPreviewOut,
    OrderUpdate,
    PlatformCourseOut,
    PublishPreview,
    PublishRequest,
    RevisionOut,
    VersionDetail,
    VersionOut,
)
from app.modules.identity.dependencies import RequestCtx
from app.modules.media.service import FileDownloadOut, PlaybackOut

router = APIRouter(tags=["courses"])


def _if_match(
    if_match: Annotated[
        str | None,
        Header(alias="If-Match", description="The course revision this edit is based on"),
    ] = None,
) -> int | None:
    if if_match is None:
        return None
    value = if_match.strip().removeprefix("W/").strip('"')
    if not value.isdigit():
        raise UnprocessableError("If-Match must be a course revision number.", code="bad_if_match")
    return int(value)


def _required_if_match(if_match: Annotated[int | None, Depends(_if_match)]) -> int:
    # Declared optional at the header level so a missing header is 428, not FastAPI's 422.
    if if_match is None:
        raise PreconditionRequiredError(
            "Send If-Match with the course revision this edit is based on.",
            code="precondition_required",
        )
    return if_match


IfMatch = Annotated[int | None, Depends(_if_match)]
RequiredIfMatch = Annotated[int, Depends(_required_if_match)]

courses = APIRouter(prefix="/courses")

# ============================================================================ courses


@courses.post("", status_code=status.HTTP_201_CREATED, operation_id="create_course")
async def create_course(ctx: RequestCtx, body: CourseCreate) -> CourseOut:
    """Create a course owned by the active organization (instructors and org admins)."""
    return await service.create_course(ctx, body)


@courses.get("", operation_id="list_courses")
async def list_courses(
    ctx: RequestCtx,
    page: PageParams,
    owned: Annotated[
        bool | None,
        Query(description="true: courses the org owns; false: courses assigned to it"),
    ] = None,
) -> CursorPage[CourseOut]:
    items, cursor = await service.list_courses(ctx, page, owned=owned)
    return CursorPage(items=items, next_cursor=cursor)


@courses.get("/{course_id}", operation_id="get_course")
async def get_course(ctx: RequestCtx, course_id: UUID) -> CourseOut:
    return await service.get_course(ctx, course_id)


@courses.patch("/{course_id}", operation_id="update_course")
async def update_course(
    ctx: RequestCtx, course_id: UUID, body: CourseUpdate, if_match: IfMatch
) -> CourseOut:
    return await service.update_course(ctx, course_id, body, if_match)


@courses.delete("/{course_id}", operation_id="archive_course")
async def archive_course(ctx: RequestCtx, course_id: UUID) -> CourseOut:
    """Archive: no more publishing or new assignments. Existing versions stay readable."""
    return await service.archive_course(ctx, course_id)


# ============================================================================ draft tree


@courses.get("/{course_id}/draft", operation_id="get_course_draft")
async def get_draft(ctx: RequestCtx, course_id: UUID) -> DraftOut:
    """The editable outline (owner-org editors)."""
    return await service.get_draft(ctx, course_id)


@courses.post(
    "/{course_id}/modules", status_code=status.HTTP_201_CREATED, operation_id="create_module"
)
async def create_module(
    ctx: RequestCtx, course_id: UUID, body: ModuleCreate, if_match: RequiredIfMatch
) -> ModuleOut:
    return await service.create_module(ctx, course_id, body.title, if_match)


@courses.put("/{course_id}/modules/order", operation_id="reorder_modules")
async def reorder_modules(
    ctx: RequestCtx, course_id: UUID, body: OrderUpdate, if_match: RequiredIfMatch
) -> DraftOut:
    """Set the module order (every module id, once)."""
    return await service.reorder_modules(ctx, course_id, body.ids, if_match)


@courses.patch("/{course_id}/modules/{module_id}", operation_id="update_module")
async def update_module(
    ctx: RequestCtx, course_id: UUID, module_id: UUID, body: ModuleUpdate, if_match: RequiredIfMatch
) -> ModuleOut:
    return await service.update_module(ctx, course_id, module_id, body.title, if_match)


@courses.delete("/{course_id}/modules/{module_id}", operation_id="delete_module")
async def delete_module(
    ctx: RequestCtx, course_id: UUID, module_id: UUID, if_match: RequiredIfMatch
) -> RevisionOut:
    return await service.delete_module(ctx, course_id, module_id, if_match)


@courses.post(
    "/{course_id}/modules/{module_id}/lessons",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_lesson",
)
async def create_lesson(
    ctx: RequestCtx, course_id: UUID, module_id: UUID, body: LessonCreate, if_match: RequiredIfMatch
) -> LessonOut:
    return await service.create_lesson(ctx, course_id, module_id, body, if_match)


@courses.put("/{course_id}/modules/{module_id}/lessons/order", operation_id="reorder_lessons")
async def reorder_lessons(
    ctx: RequestCtx, course_id: UUID, module_id: UUID, body: OrderUpdate, if_match: RequiredIfMatch
) -> DraftOut:
    """Set the module's lessons in order; lessons listed from other modules move here."""
    return await service.reorder_lessons(ctx, course_id, module_id, body.ids, if_match)


@courses.get("/{course_id}/lessons/{lesson_id}", operation_id="get_lesson")
async def get_lesson(ctx: RequestCtx, course_id: UUID, lesson_id: UUID) -> LessonOut:
    return await service.get_lesson(ctx, course_id, lesson_id)


@courses.get("/{course_id}/lessons/{lesson_id}/preview", operation_id="preview_notes")
async def preview_notes(
    ctx: RequestCtx, request: Request, course_id: UUID, lesson_id: UUID
) -> NotesPreviewOut:
    """A draft notes lesson rendered as students will see it once published (editors only)."""
    return await service.preview_notes(
        ctx,
        request.app.state.storage,
        course_id,
        lesson_id,
        request.app.state.settings.file_download_ttl_seconds,
    )


@courses.patch("/{course_id}/lessons/{lesson_id}", operation_id="update_lesson")
async def update_lesson(
    ctx: RequestCtx, course_id: UUID, lesson_id: UUID, body: LessonUpdate, if_match: RequiredIfMatch
) -> LessonOut:
    return await service.update_lesson(ctx, course_id, lesson_id, body, if_match)


@courses.delete("/{course_id}/lessons/{lesson_id}", operation_id="delete_lesson")
async def delete_lesson(
    ctx: RequestCtx, course_id: UUID, lesson_id: UUID, if_match: RequiredIfMatch
) -> RevisionOut:
    return await service.delete_lesson(ctx, course_id, lesson_id, if_match)


@courses.put("/{course_id}/lessons/{lesson_id}/skills", operation_id="set_lesson_skills")
async def set_lesson_skills(
    ctx: RequestCtx,
    course_id: UUID,
    lesson_id: UUID,
    body: LessonSkillsUpdate,
    if_match: RequiredIfMatch,
) -> LessonOut:
    return await service.set_lesson_skills(ctx, course_id, lesson_id, body.skill_ids, if_match)


# ============================================================================ publishing


@courses.get("/{course_id}/publish-preview", operation_id="get_publish_preview")
async def publish_preview(ctx: RequestCtx, course_id: UUID) -> PublishPreview:
    """The next version numbers, whether a minor is allowed (and why not), and blockers."""
    return await service.publish_preview(ctx, course_id)


@courses.post(
    "/{course_id}/versions", status_code=status.HTTP_201_CREATED, operation_id="publish_course"
)
async def publish(ctx: RequestCtx, course_id: UUID, body: PublishRequest) -> VersionOut:
    """Publish the draft as a new immutable version (minor or major)."""
    return await service.publish(ctx, course_id, body)


@courses.get("/{course_id}/versions", operation_id="list_course_versions")
async def list_versions(
    ctx: RequestCtx, course_id: UUID, page: PageParams
) -> CursorPage[VersionOut]:
    items, cursor = await service.list_versions(ctx, course_id, page)
    return CursorPage(items=items, next_cursor=cursor)


@courses.get("/{course_id}/versions/{version_id}", operation_id="get_course_version")
async def get_version(ctx: RequestCtx, course_id: UUID, version_id: UUID) -> VersionDetail:
    return await service.get_version(ctx, course_id, version_id)


_VERSION_LESSON = "/{course_id}/versions/{version_id}/lessons/{lesson_id}"


@courses.get(f"{_VERSION_LESSON}/playback", operation_id="get_version_video")
async def version_video(
    ctx: RequestCtx, request: Request, course_id: UUID, version_id: UUID, lesson_id: UUID
) -> PlaybackOut:
    """Signed playback for a video lesson of a published version, for staff who can read the
    course (owner-org editors; org admins and instructors of an assigned org)."""
    return await service.version_video(
        ctx, request.app.state.video_providers, course_id, version_id, lesson_id,
        ttl_seconds=request.app.state.settings.video_playback_ttl_seconds,
    )  # fmt: skip


@courses.get(f"{_VERSION_LESSON}/pdf", operation_id="get_version_pdf")
async def version_pdf(
    ctx: RequestCtx, request: Request, course_id: UUID, version_id: UUID, lesson_id: UUID
) -> FileDownloadOut:
    """A signed download URL for a pdf lesson of a published version (course readers' staff)."""
    return await service.version_pdf(
        ctx, request.app.state.storage, course_id, version_id, lesson_id,
        ttl_seconds=request.app.state.settings.file_download_ttl_seconds,
    )  # fmt: skip


@courses.get(f"{_VERSION_LESSON}/images", operation_id="get_version_images")
async def version_images(
    ctx: RequestCtx, request: Request, course_id: UUID, version_id: UUID, lesson_id: UUID
) -> ImageUrlsOut:
    """Signed URLs for a notes lesson's images in a published version (course readers' staff)."""
    return await service.version_images(
        ctx, request.app.state.storage, course_id, version_id, lesson_id,
        ttl_seconds=request.app.state.settings.file_download_ttl_seconds,
    )  # fmt: skip


# ============================================================================ assignments


@courses.get("/{course_id}/assignments", operation_id="list_course_assignments")
async def list_assignments(
    ctx: RequestCtx, course_id: UUID, page: PageParams
) -> CursorPage[AssignmentOut]:
    items, cursor = await service.list_assignments(ctx, course_id, page)
    return CursorPage(items=items, next_cursor=cursor)


@courses.post(
    "/{course_id}/assignments",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_course_assignments",
)
async def create_assignments(
    ctx: RequestCtx, course_id: UUID, body: AssignmentCreate
) -> list[AssignmentOut]:
    """Assign a published course to batches, or (content publishers) grant it to another org.
    A receiving org's admin uses this to distribute a granted course to their batches.
    Returns only newly created rows (existing ones are left as they are)."""
    return await service.create_assignments(ctx, course_id, body)


assignments = APIRouter(prefix="/course-assignments")


@assignments.delete(
    "/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_course_assignment",
)
async def delete_assignment(ctx: RequestCtx, assignment_id: UUID) -> None:
    await service.delete_assignment(ctx, assignment_id)


catalog = APIRouter(prefix="/catalog", tags=["catalog"])
CatalogSlug = Annotated[str, Path(min_length=1, max_length=160, pattern=r"^[a-z0-9-]+$")]


@catalog.get("", operation_id="list_catalog")
async def list_catalog(session: DbSession, page: PageParams) -> CursorPage[CatalogEntryOut]:
    """The public course catalog (no sign-in): courses published with `is_public_catalog`."""
    items, cursor = await service.list_catalog(session, page)
    return CursorPage(items=items, next_cursor=cursor)


@catalog.get("/{slug}", operation_id="get_catalog_entry")
async def get_catalog_entry(session: DbSession, slug: CatalogSlug) -> CatalogEntryOut:
    return await service.get_catalog_entry(session, slug)


platform = APIRouter(prefix="/platform", tags=["platform"])


@platform.get("/courses", operation_id="platform_list_courses")
async def platform_list_courses(
    ctx: RequestCtx,
    page: PageParams,
    organization_id: Annotated[UUID | None, Query(description="Owner organization")] = None,
    status_: Annotated[Literal["active", "archived"] | None, Query(alias="status")] = None,
) -> CursorPage[PlatformCourseOut]:
    """Every organization's courses, read-only: owner, status, version and assignment counts
    (platform admins)."""
    items, cursor = await service.platform_list_courses(
        ctx, page, organization_id=organization_id, status=status_
    )
    return CursorPage(items=items, next_cursor=cursor)


router.include_router(courses)
router.include_router(catalog)
router.include_router(assignments)
router.include_router(platform)
