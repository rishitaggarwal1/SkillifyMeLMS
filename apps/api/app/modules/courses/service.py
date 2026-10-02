"""Courses module public interface: the draft builder, publishing (minor/major), versions and
two-level assignments. Other modules (enrollments) use the functions under "Interface".

Authorization: every operation checks a permission first (clear 403s), then loads the course
through RLS. A course the caller can't see is a 404, never a 403, so its existence isn't revealed.
Editing needs the owner org (or a platform admin); assigned orgs only read published versions.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableError,
)
from app.core.pagination import CursorParams
from app.core.storage import ObjectStorage
from app.db.base import new_id
from app.db.session import run_after_commit, run_after_commit_async
from app.modules.audit import service as audit
from app.modules.courses import content_sources, events, notes
from app.modules.courses.cache import VersionCache, lesson_fields
from app.modules.courses.jobs import REVALIDATE_CATALOG
from app.modules.courses.models import (
    PLACEHOLDER_LESSON_TYPES,
    Course,
    CourseAssignment,
    CourseStatus,
    CourseVersion,
    CourseVersionLesson,
    Lesson,
    LessonType,
    ReleaseType,
)
from app.modules.courses.repository import (
    AssignmentRepository,
    CatalogRepository,
    CourseRepository,
    DraftRepository,
    VersionRepository,
)
from app.modules.courses.schemas import (
    CONTENT_MODELS,
    MAX_NOTES_BYTES,
    AssignmentCreate,
    AssignmentOut,
    CatalogEntryOut,
    CourseCounts,
    CourseCreate,
    CourseOut,
    CourseOwner,
    CourseUpdate,
    DraftModule,
    DraftOut,
    ImageUrlsOut,
    LessonCreate,
    LessonOut,
    LessonSummary,
    LessonUpdate,
    ModuleOut,
    NotesPreviewOut,
    PlatformCourseOut,
    PublishBlocker,
    PublishPreview,
    PublishRequest,
    RevisionOut,
    VersionDetail,
    VersionOut,
    VersionSummary,
)
from app.modules.courses.versioning import (
    build_snapshot,
    lesson_file_ids,
    snapshot_lessons,
    structural_changes,
    version_label,
)
from app.modules.enrollments import jobs as enrollment_jobs
from app.modules.identity import service as identity
from app.modules.identity.authz import (
    Permission,
    require_org_permission,
    require_platform_admin,
)
from app.modules.identity.dependencies import RequestContext
from app.modules.media import service as media
from app.modules.media.service import FileDownloadOut, PlaybackOut
from app.modules.skills import service as skills

Ctx = RequestContext


# ============================================================================ interface
# Used by the enrollments module. Callers pass their own RLS-scoped session.


@dataclass(frozen=True, slots=True)
class VersionRef:
    id: UUID
    course_id: UUID
    major: int
    minor: int
    title: str
    snapshot: dict[str, Any]

    @property
    def label(self) -> str:
        return version_label(self.major, self.minor)


@dataclass(frozen=True, slots=True)
class VersionLessonRef:
    lesson_id: UUID
    lesson_type: LessonType
    is_required: bool
    completion_threshold: Decimal | None
    video_asset_id: UUID | None
    video_duration_seconds: int | None = None
    file_ids: tuple[UUID, ...] = ()  # the pdf lesson's PDF, a notes lesson's images

    @property
    def counts_toward_progress(self) -> bool:
        return self.is_required and self.lesson_type not in PLACEHOLDER_LESSON_TYPES


def _ref(v: CourseVersion) -> VersionRef:
    return VersionRef(v.id, v.course_id, v.major, v.minor, v.title, v.snapshot)


async def latest_major(session: AsyncSession, course_id: UUID) -> int | None:
    """The newest major version of a course (what new enrollments get); None if unpublished."""
    latest = await VersionRepository(session).latest(course_id)
    return latest.major if latest else None


async def latest_majors(session: AsyncSession, course_ids: Sequence[UUID]) -> dict[UUID, int]:
    """Latest major per published course (unpublished courses are absent)."""
    return await VersionRepository(session).latest_majors(course_ids)


async def resolve_versions(
    session: AsyncSession, pairs: Sequence[tuple[UUID, int]], *, redis: Redis | None = None
) -> dict[tuple[UUID, int], VersionRef]:
    """What an enrollment pinned to (course, major) shows: that major's latest minor. Minor
    releases therefore reach every existing enrollment without updating them. With `redis`, served
    from the version cache (authorization is still checked on every call)."""
    if redis is not None:
        cached = await VersionCache(redis).resolve(session, pairs)
        return {
            key: VersionRef(v.id, v.course_id, v.major, v.minor, v.title, v.snapshot)
            for key, v in cached.items()
        }
    found = await VersionRepository(session).latest_for_majors(pairs)
    return {key: _ref(v) for key, v in found.items()}


async def is_latest_version(redis: Redis, course_id: UUID, major: int, version_id: UUID) -> bool:
    """Whether `version_id` is still the cached latest version of (course, major). False once a
    publish deleted the pointer (or it expired): re-validate through `resolve_version`."""
    return await VersionCache(redis).cached_latest(course_id, major) == version_id


async def resolve_version(
    session: AsyncSession, course_id: UUID, major: int, *, redis: Redis | None = None
) -> VersionRef | None:
    found = await resolve_versions(session, [(course_id, major)], redis=redis)
    return found.get((course_id, major))


async def required_lesson_ids(session: AsyncSession, version_id: UUID) -> list[UUID]:
    return await VersionRepository(session).required_lesson_ids(version_id)


async def version_lesson(
    session: AsyncSession, version_id: UUID, lesson_id: UUID, *, redis: Redis | None = None
) -> VersionLessonRef | None:
    if redis is not None:
        lessons = (await version_lessons_many(session, [version_id], redis=redis))[version_id]
        return next((lesson for lesson in lessons if lesson.lesson_id == lesson_id), None)
    row = await VersionRepository(session).lesson(version_id, lesson_id)
    if row is None:
        return None
    return _lesson_ref(row)


def _lesson_ref(row: CourseVersionLesson) -> VersionLessonRef:
    return VersionLessonRef(
        row.lesson_id, row.lesson_type, row.is_required, row.completion_threshold,
        row.video_asset_id, row.video_duration_seconds, tuple(row.file_ids or ()),
    )  # fmt: skip


async def version_lessons_many(
    session: AsyncSession, version_ids: Sequence[UUID], *, redis: Redis | None = None
) -> dict[UUID, list[VersionLessonRef]]:
    result: dict[UUID, list[VersionLessonRef]] = {vid: [] for vid in version_ids}
    if redis is not None:
        for vid, version in (await VersionCache(redis).versions(session, version_ids)).items():
            result[vid] = [VersionLessonRef(**lesson_fields(raw)) for raw in version.lessons]
        return result
    rows = await VersionRepository(session).lessons_many(version_ids)
    for row in rows:
        result[row.version_id].append(_lesson_ref(row))
    return result


async def batch_assignments(
    session: AsyncSession, course_id: UUID, organization_id: UUID
) -> dict[UUID, UUID]:
    """batch_id -> assignment_id of a course's batch assignments in one organization."""
    return await AssignmentRepository(session).batch_assignments(course_id, organization_id)


async def courses_for_batches(
    session: AsyncSession, organization_id: UUID, batch_ids: Sequence[UUID]
) -> dict[UUID, UUID]:
    """course_id -> assignment_id for courses assigned to any of these batches."""
    return await AssignmentRepository(session).courses_for_batches(organization_id, batch_ids)


async def course_titles(session: AsyncSession, course_ids: Sequence[UUID]) -> dict[UUID, str]:
    return {c.id: c.title for c in await CourseRepository(session).get_many(course_ids)}


async def readable_course(ctx: Ctx, course_id: UUID) -> Course:
    """A course the caller's org may read (404 otherwise); requires `course.read`."""
    return await _readable(ctx, course_id)


async def version_ref(
    session: AsyncSession, version_id: UUID, *, redis: Redis | None = None
) -> VersionRef | None:
    """A published version the caller may read (RLS), else None."""
    if redis is not None:
        v = (await VersionCache(redis).versions(session, [version_id])).get(version_id)
        if v is None:
            return None
        return VersionRef(v.id, v.course_id, v.major, v.minor, v.title, v.snapshot)
    version = await VersionRepository(session).get(version_id)
    return _ref(version) if version else None


def outline_lessons(version: VersionRef) -> list[tuple[str, dict[str, Any]]]:
    """(module title, lesson) for every lesson of a published version, in outline order. Read
    defensively: a snapshot is stored JSON."""
    return [
        (m.get("title", ""), lesson)
        for m in version.snapshot.get("modules") or []
        for lesson in m.get("lessons") or []
    ]


def snapshot_lesson(version: VersionRef, lesson_id: UUID) -> dict[str, Any] | None:
    """A lesson as the version published it (title, content, ...)."""
    wanted = str(lesson_id)
    return next((x for _m, x in snapshot_lessons(version.snapshot) if x["id"] == wanted), None)


@dataclass(frozen=True, slots=True)
class DraftLessonRef:
    course_id: UUID
    organization_id: UUID
    lesson_id: UUID
    lesson_type: LessonType
    title: str
    content: dict[str, Any]
    course_revision: int


async def editable_lesson(ctx: Ctx, course_id: UUID, lesson_id: UUID) -> DraftLessonRef:
    """A draft lesson of a course the caller edits (owner-org editors; 404 otherwise)."""
    course = await _editable(ctx, course_id)
    lesson = await _lesson(ctx, course_id, lesson_id)
    return DraftLessonRef(
        course.id, course.organization_id, lesson.id, lesson.lesson_type, lesson.title,
        dict(lesson.content), course.revision,
    )  # fmt: skip


async def edit_lesson_content(
    ctx: Ctx, course_id: UUID, lesson_id: UUID, content: dict[str, Any], if_match: int
) -> int:
    """For modules that own a lesson type's content (assignments): bump the course revision
    (`If-Match`, 409 when stale) and store the lesson's content reference. Returns the new
    revision. The content is the module's own reference, e.g. `{"assignment_id": ...}`."""
    course = await _editable(ctx, course_id)
    _require_active(course)
    lesson = await _lesson(ctx, course_id, lesson_id)
    parsed = CONTENT_MODELS[lesson.lesson_type].model_validate(content)
    revision = await _bump(ctx, course, if_match)
    await DraftRepository(ctx.session).update_lesson(
        lesson_id, {"content": parsed.model_dump(mode="json", exclude_none=True)}
    )
    return revision


def validate_notes_doc(doc: dict[str, Any]) -> None:
    """The notes allow-list (raises a ValueError naming the offending path)."""
    notes.validate_doc(doc)


def notes_image_ids(doc: dict[str, Any]) -> list[UUID]:
    return notes.image_file_ids(doc)


def render_notes(doc: dict[str, Any] | None) -> str:
    """Sanitized HTML for a notes document (the same renderer as notes lessons)."""
    return notes.render_html(doc)


# ============================================================================ helpers


def _is_owner(ctx: Ctx, course: Course) -> bool:
    return course.organization_id == ctx.principal.organization_id


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:120].strip("-")
    return slug or "course"


def _summary(v: CourseVersion) -> VersionSummary:
    return VersionSummary(
        id=v.id,
        major=v.major,
        minor=v.minor,
        version=version_label(v.major, v.minor),
        release_type=ReleaseType(v.release_type),
        published_at=v.published_at,
    )


def _version_out(v: CourseVersion) -> VersionOut:
    return VersionOut(
        **_summary(v).model_dump(),
        course_id=v.course_id,
        title=v.title,
        release_notes=v.release_notes,
        published_by=v.published_by,
    )


async def _courses_out(ctx: Ctx, courses: Sequence[Course]) -> list[CourseOut]:
    """Courses with their current version, in two queries."""
    version_ids = [c.current_version_id for c in courses if c.current_version_id]
    versions = {v.id: v for v in await VersionRepository(ctx.session).get_many(version_ids)}
    return [
        CourseOut(
            id=c.id,
            organization_id=c.organization_id,
            title=c.title,
            slug=c.slug,
            description=c.description,
            status=c.status,  # type: ignore[arg-type]
            is_public_catalog=c.is_public_catalog,
            revision=c.revision,
            is_owner=_is_owner(ctx, c),
            current_version=_summary(versions[c.current_version_id])
            if c.current_version_id in versions
            else None,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in courses
    ]


async def _course_out(ctx: Ctx, course: Course) -> CourseOut:
    return (await _courses_out(ctx, [course]))[0]


def _assignment_out(a: CourseAssignment) -> AssignmentOut:
    return AssignmentOut(
        id=a.id,
        course_id=a.course_id,
        organization_id=a.organization_id,
        batch_id=a.batch_id,
        kind="org_grant" if a.batch_id is None else "batch",
        assigned_by_org_id=a.assigned_by_org_id,
        parent_assignment_id=a.parent_assignment_id,
        created_at=a.created_at,
    )


async def _readable(ctx: Ctx, course_id: UUID) -> Course:
    require_org_permission(ctx.principal, Permission.COURSE_READ)
    course = await CourseRepository(ctx.session).get(course_id)
    # Readers outside the owner org only ever see published courses.
    if course is None or (not _is_owner(ctx, course) and course.current_version_id is None):
        raise NotFoundError("Course not found.")
    return course


async def _editable(ctx: Ctx, course_id: UUID) -> Course:
    require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    course = await CourseRepository(ctx.session).get(course_id)
    if course is None or not (_is_owner(ctx, course) or ctx.principal.is_platform_admin):
        raise NotFoundError("Course not found.")
    return course


async def _bump(ctx: Ctx, course: Course, if_match: int | None) -> int:
    """Start a draft edit: lock the course and bump its revision (409 if the client's is stale)."""
    revision = await CourseRepository(ctx.session).bump_revision(course.id, if_match)
    if revision is None:
        raise ConflictError(
            "The course was changed by someone else. Reload and try again.",
            code="revision_conflict",
            details={"current_revision": course.revision},
        )
    return revision


def _require_active(course: Course) -> None:
    if course.status != CourseStatus.ACTIVE:
        raise ConflictError("The course is archived.", code="course_archived")


# ============================================================================ courses


async def create_course(ctx: Ctx, data: CourseCreate) -> CourseOut:
    org_id = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    try:
        async with ctx.session.begin_nested():
            course = await CourseRepository(ctx.session).create(
                organization_id=org_id,
                title=data.title,
                slug=data.slug or _slugify(data.title),
                description=data.description,
                is_public_catalog=data.is_public_catalog,
                created_by=ctx.principal.user_id,
            )
    except IntegrityError as exc:
        raise ConflictError("A course with this slug already exists.", code="slug_taken") from exc
    out = await _course_out(ctx, course)
    await audit.record(
        ctx.session, ctx.actor, action="course.created", target_type="course",
        target_id=course.id, after=out,
    )  # fmt: skip
    return out


async def platform_list_courses(
    ctx: Ctx, params: CursorParams, *, organization_id: UUID | None, status: str | None
) -> tuple[list[PlatformCourseOut], str | None]:
    """Every organization's courses, read-only (platform admins). Owner names, versions and
    assignment counts are fetched in one query each (no N+1)."""
    require_platform_admin(ctx.principal)
    courses, cursor = await CourseRepository(ctx.session).list_all(
        params, organization_id=organization_id, status=status
    )
    ids = [c.id for c in courses]
    owner_ids = [c.organization_id for c in courses]
    owners = await identity.organization_summaries(ctx.session, owner_ids)
    version_ids = [c.current_version_id for c in courses if c.current_version_id]
    versions = {v.id: v for v in await VersionRepository(ctx.session).get_many(version_ids)}
    counts = await AssignmentRepository(ctx.session).counts_by_course(ids)
    items = []
    for c in courses:
        owner = owners.get(c.organization_id)
        grants, batches = counts[c.id]
        items.append(
            PlatformCourseOut(
                id=c.id,
                title=c.title,
                slug=c.slug,
                status=c.status,  # type: ignore[arg-type]
                owner=CourseOwner(id=c.organization_id, name=owner.name if owner else ""),
                current_version=_summary(versions[c.current_version_id])
                if c.current_version_id in versions
                else None,
                org_grant_count=grants,
                batch_assignment_count=batches,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
        )
    return items, cursor


async def course_counts(session: AsyncSession) -> CourseCounts:
    """Platform dashboard counts. The caller must be a platform admin (RLS shows all rows)."""
    return CourseCounts(courses=await CourseRepository(session).count_by_status())


async def list_courses(
    ctx: Ctx, params: CursorParams, *, owned: bool | None
) -> tuple[list[CourseOut], str | None]:
    org_id = require_org_permission(ctx.principal, Permission.COURSE_READ)
    courses, cursor = await CourseRepository(ctx.session).list_for_org(org_id, params, owned=owned)
    return await _courses_out(ctx, courses), cursor


async def get_course(ctx: Ctx, course_id: UUID) -> CourseOut:
    return await _course_out(ctx, await _readable(ctx, course_id))


async def update_course(
    ctx: Ctx, course_id: UUID, data: CourseUpdate, if_match: int | None
) -> CourseOut:
    course = await _editable(ctx, course_id)
    _require_active(course)
    await _bump(ctx, course, if_match)
    try:
        async with ctx.session.begin_nested():
            await CourseRepository(ctx.session).update(
                course_id, data.model_dump(exclude_unset=True, exclude_none=True)
            )
    except IntegrityError as exc:
        raise ConflictError("A course with this slug already exists.", code="slug_taken") from exc
    return await get_course(ctx, course_id)


async def archive_course(ctx: Ctx, course_id: UUID) -> CourseOut:
    course = await _editable(ctx, course_id)
    before = await _course_out(ctx, course)
    await _bump(ctx, course, None)
    await CourseRepository(ctx.session).update(course_id, {"status": CourseStatus.ARCHIVED})
    await CatalogRepository(ctx.session).delete(course_id)
    jobs = ctx.jobs
    run_after_commit(ctx.session, lambda: jobs.send(REVALIDATE_CATALOG))
    after = await get_course(ctx, course_id)
    await audit.record(
        ctx.session, ctx.actor, action="course.archived", target_type="course",
        target_id=course_id, before=before, after=after,
    )  # fmt: skip
    return after


# ============================================================================ draft tree


def _lesson_summary(lesson: Lesson, skill_ids: list[UUID]) -> LessonSummary:
    return LessonSummary(
        id=lesson.id,
        module_id=lesson.module_id,
        title=lesson.title,
        lesson_type=lesson.lesson_type,
        position=lesson.position,
        is_required=lesson.is_required,
        completion_threshold=lesson.completion_threshold,
        estimated_minutes=lesson.estimated_minutes,
        skill_ids=skill_ids,
    )


async def _lesson_out(ctx: Ctx, lesson: Lesson, revision: int) -> LessonOut:
    skill_ids = (await DraftRepository(ctx.session).skill_ids([lesson.id]))[lesson.id]
    return LessonOut(
        **_lesson_summary(lesson, skill_ids).model_dump(),
        content=lesson.content,
        course_revision=revision,
    )


async def get_draft(ctx: Ctx, course_id: UUID) -> DraftOut:
    course = await _editable(ctx, course_id)
    repo = DraftRepository(ctx.session)
    modules = await repo.modules(course_id)
    lessons = await repo.lessons(course_id)
    tags = await repo.skill_ids([lesson.id for lesson in lessons])
    by_module: dict[UUID, list[LessonSummary]] = {m.id: [] for m in modules}
    for lesson in lessons:
        by_module[lesson.module_id].append(_lesson_summary(lesson, tags[lesson.id]))
    return DraftOut(
        course=await _course_out(ctx, course),
        modules=[
            DraftModule(id=m.id, title=m.title, position=m.position, lessons=by_module[m.id])
            for m in modules
        ],
    )


async def create_module(ctx: Ctx, course_id: UUID, title: str, if_match: int | None) -> ModuleOut:
    course = await _editable(ctx, course_id)
    _require_active(course)
    revision = await _bump(ctx, course, if_match)
    module = await DraftRepository(ctx.session).create_module(course, title)
    return ModuleOut(
        id=module.id, title=module.title, position=module.position, course_revision=revision
    )


async def _module(ctx: Ctx, course_id: UUID, module_id: UUID) -> Any:
    module = await DraftRepository(ctx.session).get_module(course_id, module_id)
    if module is None:
        raise NotFoundError("Module not found.")
    return module


async def update_module(
    ctx: Ctx, course_id: UUID, module_id: UUID, title: str, if_match: int | None
) -> ModuleOut:
    course = await _editable(ctx, course_id)
    module = await _module(ctx, course_id, module_id)
    revision = await _bump(ctx, course, if_match)
    await DraftRepository(ctx.session).update_module(module_id, title)
    return ModuleOut(id=module_id, title=title, position=module.position, course_revision=revision)


async def delete_module(
    ctx: Ctx, course_id: UUID, module_id: UUID, if_match: int | None
) -> RevisionOut:
    """Delete a module and its lessons. Published versions keep their copies."""
    course = await _editable(ctx, course_id)
    await _module(ctx, course_id, module_id)
    revision = await _bump(ctx, course, if_match)
    repo = DraftRepository(ctx.session)
    await repo.delete_module(module_id)
    await repo.renumber_modules(course_id)
    return RevisionOut(course_revision=revision)


async def reorder_modules(
    ctx: Ctx, course_id: UUID, ids: Sequence[UUID], if_match: int | None
) -> DraftOut:
    course = await _editable(ctx, course_id)
    repo = DraftRepository(ctx.session)
    current = {m.id for m in await repo.modules(course_id)}
    if set(ids) != current:
        raise UnprocessableError(
            "Send every module of the course exactly once.", code="order_mismatch"
        )
    await _bump(ctx, course, if_match)
    await repo.set_module_order(ids)
    return await get_draft(ctx, course_id)


async def reorder_lessons(
    ctx: Ctx, course_id: UUID, module_id: UUID, ids: Sequence[UUID], if_match: int | None
) -> DraftOut:
    """Set a module's lessons in order. Lessons listed from other modules of the course move
    here and keep their ids (and so their students' progress)."""
    course = await _editable(ctx, course_id)
    await _module(ctx, course_id, module_id)
    repo = DraftRepository(ctx.session)
    lessons = {lesson.id: lesson for lesson in await repo.lessons(course_id)}
    current = {lid for lid, lesson in lessons.items() if lesson.module_id == module_id}
    if not current <= set(ids) or not set(ids) <= set(lessons):
        raise UnprocessableError(
            "Send every lesson of the module, plus any lessons of this course to move into it.",
            code="order_mismatch",
        )
    await _bump(ctx, course, if_match)
    sources = {lessons[lid].module_id for lid in ids} - {module_id}
    await repo.place_lessons(module_id, ids)
    for source in sources:
        await repo.renumber_lessons(source)
    return await get_draft(ctx, course_id)


async def _validate_content(
    ctx: Ctx, course: Course, lesson_type: LessonType, content: dict[str, Any]
) -> dict[str, Any]:
    try:
        parsed = CONTENT_MODELS[lesson_type].model_validate(content)
    except ValidationError as exc:
        raise UnprocessableError(
            "The lesson content doesn't match its type.",
            code="invalid_lesson_content",
            details=[{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()],
        ) from exc
    clean = parsed.model_dump(mode="json", exclude_none=True)
    if lesson_type == LessonType.ASSIGNMENT and clean:
        raise UnprocessableError(
            "Set an assignment's details with PUT .../lessons/{lesson_id}/assignment.",
            code="assignment_content_managed_separately",
        )
    if len(json.dumps(clean)) > MAX_NOTES_BYTES:
        raise UnprocessableError("The lesson content is too large.", code="content_too_large")
    if video_id := clean.get("video_asset_id"):
        video = (await media.videos(ctx.session, [UUID(video_id)])).get(UUID(video_id))
        if video is None or video.organization_id != course.organization_id:
            raise UnprocessableError("Unknown video.", code="invalid_video")
    if file_id := clean.get("file_id"):
        file = (await media.files(ctx.session, [UUID(file_id)])).get(UUID(file_id))
        if file is None or file.organization_id != course.organization_id or file.kind != "pdf":
            raise UnprocessableError("Unknown PDF file.", code="invalid_file")
        if not file.is_ready:
            raise UnprocessableError(
                "Confirm the PDF upload before attaching it.", code="file_not_ready"
            )
    if lesson_type == LessonType.NOTES and (doc := clean.get("doc")):
        image_ids = notes.image_file_ids(doc)
        found = await media.files(ctx.session, image_ids)
        bad = [
            str(i)
            for i in image_ids
            if (f := found.get(i)) is None
            or f.organization_id != course.organization_id
            or f.kind != "image"
            or not f.is_ready
        ]
        if bad:
            raise UnprocessableError(
                "Notes images must be confirmed image uploads of this organization.",
                code="invalid_image",
                details={"file_ids": bad},
            )
    return clean


def _lesson_settings(lesson_type: LessonType, values: dict[str, Any]) -> dict[str, Any]:
    if lesson_type in PLACEHOLDER_LESSON_TYPES:
        values["is_required"] = False  # placeholders don't count until their phase lands
    if lesson_type != LessonType.VIDEO:
        values.pop("completion_threshold", None)
    return values


async def create_lesson(
    ctx: Ctx, course_id: UUID, module_id: UUID, data: LessonCreate, if_match: int | None
) -> LessonOut:
    course = await _editable(ctx, course_id)
    _require_active(course)
    module = await _module(ctx, course_id, module_id)
    content = await _validate_content(ctx, course, data.lesson_type, data.content)
    revision = await _bump(ctx, course, if_match)
    values = _lesson_settings(
        data.lesson_type,
        {
            "title": data.title,
            "is_required": data.is_required,
            "completion_threshold": data.completion_threshold,
            "estimated_minutes": data.estimated_minutes,
        },
    )
    lesson = await DraftRepository(ctx.session).create_lesson(
        module,
        lesson_type=data.lesson_type,
        content=content,
        created_by=ctx.principal.user_id,
        **values,
    )
    return await _lesson_out(ctx, lesson, revision)


async def _lesson(ctx: Ctx, course_id: UUID, lesson_id: UUID) -> Lesson:
    lesson = await DraftRepository(ctx.session).get_lesson(course_id, lesson_id)
    if lesson is None:
        raise NotFoundError("Lesson not found.")
    return lesson


async def get_lesson(ctx: Ctx, course_id: UUID, lesson_id: UUID) -> LessonOut:
    course = await _editable(ctx, course_id)
    return await _lesson_out(ctx, await _lesson(ctx, course_id, lesson_id), course.revision)


async def preview_notes(
    ctx: Ctx, storage: ObjectStorage, course_id: UUID, lesson_id: UUID, ttl_seconds: int
) -> NotesPreviewOut:
    """The draft notes lesson rendered exactly as publishing would, with signed image URLs."""
    await _editable(ctx, course_id)
    lesson = await _lesson(ctx, course_id, lesson_id)
    if lesson.lesson_type != LessonType.NOTES:
        raise NotFoundError("Notes lesson not found.")
    doc = lesson.content.get("doc")
    images = await media.download_urls(
        ctx.session, storage, notes.image_file_ids(doc) if doc else [], ttl_seconds
    )
    return NotesPreviewOut(
        html=notes.render_html(doc),
        image_urls={file_id: d.url for file_id, d in images.items()},
        expires_at=min((d.expires_at for d in images.values()), default=None),
    )


async def update_lesson(
    ctx: Ctx, course_id: UUID, lesson_id: UUID, data: LessonUpdate, if_match: int | None
) -> LessonOut:
    course = await _editable(ctx, course_id)
    lesson = await _lesson(ctx, course_id, lesson_id)
    values = data.model_dump(exclude_unset=True, exclude_none=True)
    if data.content is not None:
        values["content"] = await _validate_content(ctx, course, lesson.lesson_type, data.content)
    revision = await _bump(ctx, course, if_match)
    await DraftRepository(ctx.session).update_lesson(
        lesson_id, _lesson_settings(lesson.lesson_type, values)
    )
    return await _lesson_out(ctx, await _lesson(ctx, course_id, lesson_id), revision)


async def delete_lesson(
    ctx: Ctx, course_id: UUID, lesson_id: UUID, if_match: int | None
) -> RevisionOut:
    course = await _editable(ctx, course_id)
    lesson = await _lesson(ctx, course_id, lesson_id)
    revision = await _bump(ctx, course, if_match)
    repo = DraftRepository(ctx.session)
    await repo.delete_lesson(lesson_id)
    await repo.renumber_lessons(lesson.module_id)
    return RevisionOut(course_revision=revision)


async def set_lesson_skills(
    ctx: Ctx, course_id: UUID, lesson_id: UUID, skill_ids: Sequence[UUID], if_match: int | None
) -> LessonOut:
    course = await _editable(ctx, course_id)
    await _lesson(ctx, course_id, lesson_id)
    unknown = set(skill_ids) - await skills.existing_skill_ids(ctx.session, skill_ids)
    if unknown:
        raise UnprocessableError(
            "Unknown skills.", code="unknown_skills", details={"skill_ids": sorted(unknown)}
        )
    revision = await _bump(ctx, course, if_match)
    await DraftRepository(ctx.session).replace_skills(lesson_id, course.organization_id, skill_ids)
    return await _lesson_out(ctx, await _lesson(ctx, course_id, lesson_id), revision)


# ============================================================================ publishing


# Publish blocker for a sourced lesson type whose content isn't ready.
_NOT_READY: dict[LessonType, Literal["assignment_not_ready"]] = {
    LessonType.ASSIGNMENT: "assignment_not_ready"
}


@dataclass(frozen=True, slots=True)
class _Draft:
    snapshot: dict[str, Any]
    lessons: list[Lesson]
    module_positions: dict[UUID, int]
    video_durations: dict[UUID, int | None]
    skill_ids: dict[UUID, list[UUID]]
    blockers: list[PublishBlocker]


async def _draft(ctx: Ctx, course: Course) -> _Draft:
    repo = DraftRepository(ctx.session)
    modules = await repo.modules(course.id)
    lessons = await repo.lessons(course.id)
    tags = await repo.skill_ids([lesson.id for lesson in lessons])

    def ref(lesson: Lesson, key: str) -> UUID | None:
        value = lesson.content.get(key)
        return UUID(value) if value else None

    video_ids = [v for lesson in lessons if (v := ref(lesson, "video_asset_id"))]
    file_ids = [f for lesson in lessons if (f := ref(lesson, "file_id"))]
    videos = await media.videos(ctx.session, video_ids)
    files = await media.files(ctx.session, file_ids)

    blockers: list[PublishBlocker] = []
    if course.status != CourseStatus.ACTIVE:
        blockers.append(PublishBlocker(code="course_archived"))
    if not lessons:
        blockers.append(PublishBlocker(code="empty_course"))
    videos_pending = [
        lesson.id
        for lesson in lessons
        if lesson.lesson_type == LessonType.VIDEO
        and not ((v := ref(lesson, "video_asset_id")) and v in videos and videos[v].is_ready)
    ]
    pdfs_pending = [
        lesson.id
        for lesson in lessons
        if lesson.lesson_type == LessonType.PDF
        and not ((f := ref(lesson, "file_id")) and f in files and files[f].is_ready)
    ]
    if videos_pending:
        blockers.append(PublishBlocker(code="video_not_ready", lesson_ids=videos_pending))
    if pdfs_pending:
        blockers.append(PublishBlocker(code="pdf_not_ready", lesson_ids=pdfs_pending))

    # Lessons whose content another module holds (assignments) publish what it returns. No
    # registered source fails closed (ContentSourceMissingError), never skips the check.
    sourced: dict[UUID, dict[str, Any]] = {}
    for lesson_type in content_sources.SOURCED_TYPES:
        ids = [x.id for x in lessons if x.lesson_type == lesson_type]
        if not ids:
            continue
        source = content_sources.required_source(lesson_type)
        found = await source.published(ctx.session, ids)
        sourced.update(found)
        missing = [lesson_id for lesson_id in ids if lesson_id not in found]
        if missing:
            blockers.append(PublishBlocker(code=_NOT_READY[lesson_type], lesson_ids=missing))

    durations = {vid: info.duration_seconds for vid, info in videos.items()}
    return _Draft(
        snapshot=build_snapshot(course, modules, lessons, tags, durations, sourced_content=sourced),
        lessons=lessons,
        module_positions={m.id: m.position for m in modules},
        video_durations=durations,
        skill_ids=tags,
        blockers=blockers,
    )


async def publish_preview(ctx: Ctx, course_id: UUID) -> PublishPreview:
    course = await _editable(ctx, course_id)
    draft = await _draft(ctx, course)
    latest = await VersionRepository(ctx.session).latest(course_id)
    if latest is None:
        return PublishPreview(
            is_first_release=True, next_major="1.0", next_minor=None, minor_allowed=False,
            structural_changes=[], blockers=draft.blockers,
        )  # fmt: skip
    changes = structural_changes(latest.snapshot, draft.snapshot)
    return PublishPreview(
        is_first_release=False,
        next_major=version_label(latest.major + 1, 0),
        next_minor=version_label(latest.major, latest.minor + 1),
        minor_allowed=not changes,
        structural_changes=changes,
        blockers=draft.blockers,
    )


async def publish(ctx: Ctx, course_id: UUID, data: PublishRequest) -> VersionOut:
    """Snapshot the draft as an immutable version. The first release is always 1.0; after that a
    minor (content corrections only) is rejected if anything structural changed."""
    course = await _editable(ctx, course_id)
    await _bump(ctx, course, None)  # locks the course: concurrent publishes serialize
    draft = await _draft(ctx, course)
    if draft.blockers:
        raise ConflictError(
            "The course can't be published yet.",
            code="publish_blocked",
            details=[b.model_dump(mode="json") for b in draft.blockers],
        )
    versions = VersionRepository(ctx.session)
    latest = await versions.latest(course_id)
    if latest is None:
        major, minor, release_type = 1, 0, ReleaseType.MAJOR
    elif data.release_type == ReleaseType.MAJOR:
        major, minor, release_type = latest.major + 1, 0, ReleaseType.MAJOR
    else:
        changes = structural_changes(latest.snapshot, draft.snapshot)
        if changes:
            raise ConflictError(
                "Structural changes need a major version.",
                code="minor_not_allowed",
                details=[c.model_dump(mode="json") for c in changes],
            )
        major, minor, release_type = latest.major, latest.minor + 1, ReleaseType.MINOR

    version = CourseVersion(
        id=new_id(),
        course_id=course.id,
        organization_id=course.organization_id,
        major=major,
        minor=minor,
        release_type=release_type,
        title=course.title,
        snapshot=draft.snapshot,
        release_notes=data.release_notes,
        published_by=ctx.principal.user_id,
    )
    rows = [
        CourseVersionLesson(
            version_id=version.id,
            lesson_id=lesson.id,
            course_id=course.id,
            organization_id=course.organization_id,
            module_id=lesson.module_id,
            module_position=draft.module_positions[lesson.module_id],
            position=lesson.position,
            lesson_type=lesson.lesson_type,
            is_required=lesson.is_required,
            completion_threshold=lesson.completion_threshold,
            video_asset_id=(v := lesson.content.get("video_asset_id")) and UUID(v),
            video_duration_seconds=draft.video_durations.get(UUID(v)) if v else None,
            file_ids=lesson_file_ids(lesson.lesson_type, lesson.content),
        )
        for lesson in draft.lessons
    ]
    version = await versions.create(version, rows)
    await CourseRepository(ctx.session).update(course.id, {"current_version_id": version.id})
    await _update_catalog(ctx, course, version, draft)
    events.course_published(ctx.session, version, is_public_catalog=course.is_public_catalog)
    # After commit: readers resolving this major must see the new version, and the public catalog
    # pages must be regenerated. Neither may happen for a publish that rolls back.
    cache, jobs, major = VersionCache(ctx.redis), ctx.jobs, version.major
    run_after_commit_async(ctx.session, lambda: cache.invalidate_pointer(course.id, major))
    run_after_commit(ctx.session, lambda: jobs.send(REVALIDATE_CATALOG))
    out = _version_out(version)
    await audit.record(
        ctx.session, ctx.actor, action="course.published", target_type="course",
        target_id=course.id, after=out,
    )  # fmt: skip
    return out


async def _update_catalog(ctx: Ctx, course: Course, version: CourseVersion, draft: _Draft) -> None:
    catalog = CatalogRepository(ctx.session)
    if not course.is_public_catalog:
        await catalog.delete(course.id)
        return
    all_skills = sorted({s for ids in draft.skill_ids.values() for s in ids})
    names = await skills.skill_names(ctx.session, all_skills)
    await catalog.upsert(
        course_id=course.id,
        organization_id=course.organization_id,
        version_id=version.id,
        slug=f"{course.slug}-{course.id.hex[-6:]}",
        title=course.title,
        description=course.description,
        skill_names=sorted(set(names.values())),
        lesson_count=len(snapshot_lessons(draft.snapshot)),
        published_at=version.published_at,
    )


async def list_versions(
    ctx: Ctx, course_id: UUID, params: CursorParams
) -> tuple[list[VersionOut], str | None]:
    await _readable(ctx, course_id)
    versions, cursor = await VersionRepository(ctx.session).list_page(course_id, params)
    return [_version_out(v) for v in versions], cursor


async def get_version(ctx: Ctx, course_id: UUID, version_id: UUID) -> VersionDetail:
    await _readable(ctx, course_id)
    version = await VersionRepository(ctx.session).get(version_id)
    if version is None or version.course_id != course_id:
        raise NotFoundError("Version not found.")
    return VersionDetail(**_version_out(version).model_dump(), snapshot=version.snapshot)


# ============================================================================ public catalog
# No sign-in and no tenant context: catalog_entries holds public fields only and is readable by
# everyone (RLS `USING (true)`). Entries are written on publish and removed on archive.


async def list_catalog(
    session: AsyncSession, params: CursorParams
) -> tuple[list[CatalogEntryOut], str | None]:
    rows, cursor = await CatalogRepository(session).list_page(params)
    return [CatalogEntryOut.model_validate(r) for r in rows], cursor


async def get_catalog_entry(session: AsyncSession, slug: str) -> CatalogEntryOut:
    entry = await CatalogRepository(session).by_slug(slug)
    if entry is None:
        raise NotFoundError("Course not found.")
    return CatalogEntryOut.model_validate(entry)


# ============================================================================ reader content
# Staff of an org that reads the course (owner-org editors, and org_admins and instructors of an
# assigned org) may open any published version's lesson content. RLS (`app.video_readable`,
# `app.file_readable`) enforces the same rule underneath.


async def _reader_lesson(
    ctx: Ctx, course_id: UUID, version_id: UUID, lesson_id: UUID, lesson_type: LessonType
) -> VersionLessonRef:
    await _readable(ctx, course_id)
    version = await VersionRepository(ctx.session).get(version_id)
    if version is None or version.course_id != course_id:
        raise NotFoundError("Version not found.")
    lesson = await version_lesson(ctx.session, version_id, lesson_id)
    if lesson is None or lesson.lesson_type != lesson_type:
        raise NotFoundError("Lesson not found.")
    return lesson


async def version_video(
    ctx: Ctx,
    providers: media.Providers,
    course_id: UUID,
    version_id: UUID,
    lesson_id: UUID,
    *,
    ttl_seconds: int,
) -> PlaybackOut:
    lesson = await _reader_lesson(ctx, course_id, version_id, lesson_id, LessonType.VIDEO)
    playback = (
        await media.playback_for(ctx.session, providers, lesson.video_asset_id, ttl_seconds)
        if lesson.video_asset_id
        else None
    )
    if playback is None:
        raise NotFoundError("Video not found.")
    return PlaybackOut(url=playback.url, kind=playback.kind, expires_at=playback.expires_at)


async def version_pdf(
    ctx: Ctx,
    storage: ObjectStorage,
    course_id: UUID,
    version_id: UUID,
    lesson_id: UUID,
    *,
    ttl_seconds: int,
) -> FileDownloadOut:
    lesson = await _reader_lesson(ctx, course_id, version_id, lesson_id, LessonType.PDF)
    found = await media.download_urls(ctx.session, storage, lesson.file_ids[:1], ttl_seconds)
    if not found:
        raise NotFoundError("PDF not found.")
    download = next(iter(found.values()))
    return FileDownloadOut(
        url=download.url, file_name=download.file_name, expires_at=download.expires_at
    )


async def version_images(
    ctx: Ctx,
    storage: ObjectStorage,
    course_id: UUID,
    version_id: UUID,
    lesson_id: UUID,
    *,
    ttl_seconds: int,
) -> ImageUrlsOut:
    lesson = await _reader_lesson(ctx, course_id, version_id, lesson_id, LessonType.NOTES)
    found = await media.download_urls(ctx.session, storage, lesson.file_ids, ttl_seconds)
    return ImageUrlsOut(
        urls={file_id: d.url for file_id, d in found.items()},
        expires_at=min((d.expires_at for d in found.values()), default=None),
    )


# ============================================================================ assignments


async def list_assignments(
    ctx: Ctx, course_id: UUID, params: CursorParams
) -> tuple[list[AssignmentOut], str | None]:
    """Owner editors see every assignment of the course; a receiving org's staff see theirs."""
    await _readable(ctx, course_id)
    rows, cursor = await AssignmentRepository(ctx.session).list_page(course_id, params)
    return [_assignment_out(a) for a in rows], cursor


async def create_assignments(
    ctx: Ctx, course_id: UUID, data: AssignmentCreate
) -> list[AssignmentOut]:
    """Assign a published course.

    - Owner-org editors (publisher-made rows): to their own org's batches, or, for content
      publishers, to another org (an org grant) or directly to its batches.
    - A receiving org's org_admin: narrow their org grant to their own batches (never widen).
    """
    course = await _readable(ctx, course_id)
    active_org = ctx.principal.organization_id
    assert active_org is not None  # noqa: S101 - _readable required an org
    if course.status != CourseStatus.ACTIVE:
        raise ConflictError("The course is archived.", code="course_archived")
    if course.current_version_id is None:
        raise ConflictError("Publish the course before assigning it.", code="course_not_published")
    owner_side = _is_owner(ctx, course) or ctx.principal.is_platform_admin
    target = data.organization_id or (course.organization_id if owner_side else active_org)

    if owner_side:
        require_org_permission(ctx.principal, Permission.COURSE_ASSIGN)
        rows = await _publisher_rows(ctx, course, target, data.batch_ids)
    else:
        require_org_permission(ctx.principal, Permission.COURSE_DISTRIBUTE)
        rows = await _narrowing_rows(ctx, course, target, data.batch_ids)

    try:
        async with ctx.session.begin_nested():
            created = await AssignmentRepository(ctx.session).insert_many(rows)
    except IntegrityError as exc:  # composite FK: the batch isn't in the receiving org
        raise UnprocessableError(
            "Batches must belong to the receiving organization.", code="invalid_batch"
        ) from exc
    out = [_assignment_out(a) for a in created]
    if any(a.batch_id for a in created):
        enrollment_jobs.enqueue_reconcile(ctx.session, ctx.jobs, course.id, target)
    if out:
        await audit.record(
            ctx.session, ctx.actor, action="course.assigned", target_type="course",
            target_id=course.id, after=out,
        )  # fmt: skip
    return out


async def _publisher_rows(
    ctx: Ctx, course: Course, target: UUID, batch_ids: Sequence[UUID]
) -> list[dict[str, Any]]:
    owner = course.organization_id
    if target != owner and not await identity.org_is_content_publisher(ctx.session, owner):
        raise PermissionDeniedError(
            "Only content publishers can assign courses to other organizations.",
            code="content_publisher_required",
        )
    if target == owner:
        if not batch_ids:
            raise UnprocessableError(
                "Choose the batches of your organization that should get this course.",
                code="batch_required",
            )
        await _require_batches_in(ctx, target, batch_ids)
    base = {
        "course_id": course.id,
        "owner_organization_id": owner,
        "organization_id": target,
        "assigned_by_org_id": owner,
        "parent_assignment_id": None,
        "created_by": ctx.principal.user_id,
    }
    if not batch_ids:
        return [{**base, "batch_id": None}]  # org grant
    return [{**base, "batch_id": batch_id} for batch_id in batch_ids]


async def _narrowing_rows(
    ctx: Ctx, course: Course, target: UUID, batch_ids: Sequence[UUID]
) -> list[dict[str, Any]]:
    if target != ctx.principal.organization_id:
        raise PermissionDeniedError(
            "You can only distribute courses to your own organization's batches.",
            code="cannot_widen_assignment",
        )
    if not batch_ids:
        raise UnprocessableError("Choose the batches to assign.", code="batch_required")
    grant = await AssignmentRepository(ctx.session).org_grant(course.id, target)
    if grant is None:
        raise PermissionDeniedError(
            "Your organization can only distribute courses granted to it.",
            code="cannot_widen_assignment",
        )
    await _require_batches_in(ctx, target, batch_ids)
    return [
        {
            "course_id": course.id,
            "owner_organization_id": course.organization_id,
            "organization_id": target,
            "batch_id": batch_id,
            "assigned_by_org_id": target,
            "parent_assignment_id": grant.id,
            "created_by": ctx.principal.user_id,
        }
        for batch_id in batch_ids
    ]


async def _require_batches_in(ctx: Ctx, org_id: UUID, batch_ids: Sequence[UUID]) -> None:
    found = await identity.batch_ids_in_org(ctx.session, org_id, batch_ids)
    if missing := [b for b in batch_ids if b not in found]:
        raise UnprocessableError(
            "Batches must belong to the receiving organization.",
            code="invalid_batch",
            details={"batch_ids": missing},
        )


async def delete_assignment(ctx: Ctx, assignment_id: UUID) -> None:
    """Remove an assignment. Only the org that created a row can remove it: the publisher its
    rows (an org grant also removes the batch rows narrowed from it), a receiving org_admin the
    rows their org created."""
    require_org_permission(ctx.principal, Permission.COURSE_READ)
    repo = AssignmentRepository(ctx.session)
    assignment = await repo.get(assignment_id)
    if assignment is None:
        raise NotFoundError("Assignment not found.")
    active = ctx.principal.organization_id
    publisher_made = assignment.assigned_by_org_id == assignment.owner_organization_id
    if ctx.principal.is_platform_admin:
        allowed = True
    elif publisher_made:
        allowed = active == assignment.owner_organization_id and (
            Permission.COURSE_ASSIGN in ctx.principal.permissions
        )
    else:
        allowed = active == assignment.organization_id and (
            Permission.COURSE_DISTRIBUTE in ctx.principal.permissions
        )
    if not allowed:
        raise PermissionDeniedError(
            "Only the organization that made this assignment can remove it.",
            code="assignment_not_yours",
        )
    before = _assignment_out(assignment)
    if not await repo.delete(assignment_id):
        raise NotFoundError("Assignment not found.")
    enrollment_jobs.enqueue_reconcile(
        ctx.session, ctx.jobs, assignment.course_id, assignment.organization_id
    )
    await audit.record(
        ctx.session, ctx.actor, action="course.unassigned", target_type="course",
        target_id=assignment.course_id, before=before,
    )  # fmt: skip
