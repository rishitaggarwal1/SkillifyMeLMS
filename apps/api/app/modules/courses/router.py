"""Courses HTTP API: builder, publishing, versions and assignments.

Draft edits accept an optional `If-Match: <revision>` header (the course revision the client
started from). A stale revision gets `409 revision_conflict`, so concurrent editors never silently
overwrite each other.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status

from app.core.errors import UnprocessableError
from app.core.pagination import CursorPage, PageParams
from app.modules.courses import service
from app.modules.courses.schemas import (
    AssignmentCreate,
    AssignmentOut,
    CourseCreate,
    CourseOut,
    CourseUpdate,
    DraftOut,
    LessonCreate,
    LessonOut,
    LessonSkillsUpdate,
    LessonUpdate,
    ModuleCreate,
    ModuleOut,
    ModuleUpdate,
    OrderUpdate,
    PublishPreview,
    PublishRequest,
    RevisionOut,
    VersionDetail,
    VersionOut,
)
from app.modules.identity.dependencies import RequestCtx

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


IfMatch = Annotated[int | None, Depends(_if_match)]

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
    ctx: RequestCtx, course_id: UUID, body: ModuleCreate, if_match: IfMatch
) -> ModuleOut:
    return await service.create_module(ctx, course_id, body.title, if_match)


@courses.put("/{course_id}/modules/order", operation_id="reorder_modules")
async def reorder_modules(
    ctx: RequestCtx, course_id: UUID, body: OrderUpdate, if_match: IfMatch
) -> DraftOut:
    """Set the module order (every module id, once)."""
    return await service.reorder_modules(ctx, course_id, body.ids, if_match)


@courses.patch("/{course_id}/modules/{module_id}", operation_id="update_module")
async def update_module(
    ctx: RequestCtx, course_id: UUID, module_id: UUID, body: ModuleUpdate, if_match: IfMatch
) -> ModuleOut:
    return await service.update_module(ctx, course_id, module_id, body.title, if_match)


@courses.delete("/{course_id}/modules/{module_id}", operation_id="delete_module")
async def delete_module(
    ctx: RequestCtx, course_id: UUID, module_id: UUID, if_match: IfMatch
) -> RevisionOut:
    return await service.delete_module(ctx, course_id, module_id, if_match)


@courses.post(
    "/{course_id}/modules/{module_id}/lessons",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_lesson",
)
async def create_lesson(
    ctx: RequestCtx, course_id: UUID, module_id: UUID, body: LessonCreate, if_match: IfMatch
) -> LessonOut:
    return await service.create_lesson(ctx, course_id, module_id, body, if_match)


@courses.put("/{course_id}/modules/{module_id}/lessons/order", operation_id="reorder_lessons")
async def reorder_lessons(
    ctx: RequestCtx, course_id: UUID, module_id: UUID, body: OrderUpdate, if_match: IfMatch
) -> DraftOut:
    """Set the module's lessons in order; lessons listed from other modules move here."""
    return await service.reorder_lessons(ctx, course_id, module_id, body.ids, if_match)


@courses.get("/{course_id}/lessons/{lesson_id}", operation_id="get_lesson")
async def get_lesson(ctx: RequestCtx, course_id: UUID, lesson_id: UUID) -> LessonOut:
    return await service.get_lesson(ctx, course_id, lesson_id)


@courses.patch("/{course_id}/lessons/{lesson_id}", operation_id="update_lesson")
async def update_lesson(
    ctx: RequestCtx, course_id: UUID, lesson_id: UUID, body: LessonUpdate, if_match: IfMatch
) -> LessonOut:
    return await service.update_lesson(ctx, course_id, lesson_id, body, if_match)


@courses.delete("/{course_id}/lessons/{lesson_id}", operation_id="delete_lesson")
async def delete_lesson(
    ctx: RequestCtx, course_id: UUID, lesson_id: UUID, if_match: IfMatch
) -> RevisionOut:
    return await service.delete_lesson(ctx, course_id, lesson_id, if_match)


@courses.put("/{course_id}/lessons/{lesson_id}/skills", operation_id="set_lesson_skills")
async def set_lesson_skills(
    ctx: RequestCtx,
    course_id: UUID,
    lesson_id: UUID,
    body: LessonSkillsUpdate,
    if_match: IfMatch,
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


router.include_router(courses)
router.include_router(assignments)
