"""Enrollments module public interface: enrollment fan-out (reconciliation), the student's
learning APIs, completion and progress, and org_admin opt-in to a new major version.

**Enrollments follow batch assignments.** Instead of replaying individual events, every trigger
(an assignment created or removed, a student joining or leaving a batch) *reconciles* against the
current state: students covered by a batch assignment get an active enrollment, and active
enrollments no longer covered are revoked (progress kept, so re-assignment restores it). This is
idempotent and order-independent, so redelivered or out-of-order events are harmless.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.logging import get_logger
from app.core.pagination import CursorParams
from app.core.storage import ObjectStorage
from app.db.tenancy import set_tenant_context
from app.modules.audit import service as audit
from app.modules.courses import service as courses
from app.modules.courses.models import LessonType
from app.modules.courses.service import VersionLessonRef, VersionRef
from app.modules.enrollments import completion_sources, events
from app.modules.enrollments import jobs as enrollment_jobs
from app.modules.enrollments.heartbeat_cache import HeartbeatCache, HeartbeatCheck
from app.modules.enrollments.models import Enrollment, EnrollmentStatus, LessonProgress
from app.modules.enrollments.progress import (
    CompletionRule,
    completion_rule,
    course_percent,
    video_watched,
)
from app.modules.enrollments.repository import (
    EnrollmentRepository,
    LessonProgressRepository,
    UpsertedEnrollment,
)
from app.modules.enrollments.schemas import (
    EnrollmentCounts,
    EnrollmentDetail,
    EnrollmentOut,
    EnrollmentVersion,
    LessonCompletionOut,
    LessonImagesOut,
    LessonProgressOut,
    UpgradeAccepted,
    UpgradeRequest,
    VideoHeartbeat,
    VideoResume,
)
from app.modules.enrollments.video_buffer import VideoBuffer, buffer_key
from app.modules.identity import service as identity
from app.modules.identity.authz import Permission, require_org, require_org_permission
from app.modules.identity.dependencies import RequestContext
from app.modules.media import service as media
from app.modules.media.service import FileDownloadOut, PlaybackOut

logger = get_logger(__name__)
Ctx = RequestContext
CHUNK = 500


async def _video_lesson(
    ctx: Ctx, enrollment_id: UUID, lesson_id: UUID
) -> tuple[Enrollment, VersionLessonRef]:
    enrollment, _, lesson = await _video_lesson_in(ctx, enrollment_id, lesson_id)
    return enrollment, lesson


async def _video_lesson_in(
    ctx: Ctx, enrollment_id: UUID, lesson_id: UUID
) -> tuple[Enrollment, VersionRef, VersionLessonRef]:
    enrollment, version = await _my_enrollment(ctx, enrollment_id)
    lesson = await _lesson_in_version(ctx, version, lesson_id)
    if lesson.lesson_type != "video" or not lesson.video_asset_id:
        raise NotFoundError("Video lesson not found.")
    if not lesson.video_duration_seconds:
        raise ConflictError("The video isn't ready.", code="video_not_ready")
    return enrollment, version, lesson


async def video_playback(
    ctx: Ctx, providers: media.Providers, enrollment_id: UUID, lesson_id: UUID, ttl: int
) -> PlaybackOut:
    _, lesson = await _video_lesson(ctx, enrollment_id, lesson_id)
    assert lesson.video_asset_id is not None  # noqa: S101
    playback = await media.playback_for(ctx.session, providers, lesson.video_asset_id, ttl)
    if playback is None:
        raise NotFoundError("Video not found.")
    return PlaybackOut(url=playback.url, kind=playback.kind, expires_at=playback.expires_at)


async def video_resume(ctx: Ctx, redis: Redis, enrollment_id: UUID, lesson_id: UUID) -> VideoResume:
    _, lesson = await _video_lesson(ctx, enrollment_id, lesson_id)
    asset_id = lesson.video_asset_id
    assert asset_id is not None  # noqa: S101
    await LessonProgressRepository(ctx.session).reset_replaced_videos(
        enrollment_id, {lesson_id: asset_id}
    )
    buffered = await VideoBuffer(redis).read(buffer_key(enrollment_id, lesson_id, asset_id))
    if buffered:
        return VideoResume(
            video_asset_id=asset_id,
            position_seconds=buffered.data["position"],
            watched_ratio=buffered.ratio,
        )
    progress = await LessonProgressRepository(ctx.session).get(enrollment_id, lesson_id)
    matching = progress is not None and progress.video_asset_id == asset_id
    return VideoResume(
        video_asset_id=asset_id,
        position_seconds=(progress.video_position_seconds or 0) if matching and progress else 0,
        watched_ratio=float(progress.watched_ratio or 0) if matching and progress else 0,
    )


async def video_heartbeat(
    ctx: Ctx, redis: Redis, data: VideoHeartbeat, *, interval_seconds: float
) -> None:
    """Buffer one heartbeat. The authorization check is cached briefly (see heartbeat_cache), so a
    steady heartbeat touches only Redis; Postgres is read on a cache miss and to seed a new buffer
    entry's baseline."""
    principal = ctx.principal
    cache = HeartbeatCache(redis)
    check = await cache.get(data.enrollment_id, data.lesson_id)
    if (
        check is None
        or check.user_id != principal.user_id
        or check.organization_id != principal.organization_id
    ):
        enrollment, version, lesson = await _video_lesson_in(
            ctx, data.enrollment_id, data.lesson_id
        )
        assert lesson.video_asset_id is not None  # noqa: S101
        assert lesson.video_duration_seconds is not None  # noqa: S101
        check = HeartbeatCheck(
            user_id=enrollment.user_id,
            organization_id=enrollment.organization_id,
            course_id=version.course_id,
            major=version.major,
            version_id=version.id,
            video_asset_id=lesson.video_asset_id,
            duration_seconds=lesson.video_duration_seconds,
        )
        await cache.put(data.enrollment_id, data.lesson_id, check)
    if data.video_asset_id != check.video_asset_id:
        raise ConflictError("The video changed. Reload the lesson.", code="video_changed")
    if data.position_seconds > check.duration_seconds + 1:
        raise UnprocessableError("Position exceeds the video duration.")
    buffer = VideoBuffer(redis)
    key = buffer_key(data.enrollment_id, data.lesson_id, data.video_asset_id)
    fields = {
        "enrollment_id": str(data.enrollment_id),
        "lesson_id": str(data.lesson_id),
        "asset_id": str(data.video_asset_id),
        "organization_id": str(check.organization_id),
        "user_id": str(check.user_id),
        "duration": check.duration_seconds,
        "position": min(data.position_seconds, check.duration_seconds),
        "played": data.played_seconds,
        "rate": data.playback_rate,
        "at": datetime.now(UTC).isoformat(),
    }
    if await buffer.write(key, fields, None, interval_seconds=interval_seconds):
        return
    # First heartbeat for this entry: seed the watched bitmap from Postgres.
    progress = await LessonProgressRepository(ctx.session).get(data.enrollment_id, data.lesson_id)
    baseline = (
        progress.watched_segments or b""
        if progress is not None and progress.video_asset_id == data.video_asset_id
        else b""
    )
    await buffer.write(key, fields, baseline, interval_seconds=interval_seconds)


# ============================================================================ fan-out


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    enrolled: int = 0
    reactivated: int = 0
    revoked: int = 0


def _emit_created(session: AsyncSession, org_id: UUID, rows: Sequence[UpsertedEnrollment],
                  assignments: dict[UUID, UUID]) -> None:  # fmt: skip
    for row in rows:
        if row.created:
            events.enrollment_created(
                session, enrollment_id=row.id, organization_id=org_id, user_id=row.user_id,
                course_id=row.course_id, major_version=row.major_version,
                assignment_id=assignments.get(row.course_id),
            )  # fmt: skip


async def reconcile_course_org(
    session: AsyncSession, course_id: UUID, organization_id: UUID
) -> ReconcileResult:
    """Make a course's enrollments in one org match its batch assignments there. New enrollments
    get the course's latest major version. Runs as a system job (platform RLS context)."""
    major = await courses.latest_major(session, course_id)
    covered: dict[UUID, UUID] = {}  # student -> the assignment covering them
    for batch_id, assignment_id in (
        await courses.batch_assignments(session, course_id, organization_id)
    ).items():
        async for page in identity.iter_batch_student_ids(session, batch_id, page_size=CHUNK):
            for user_id in page:
                covered.setdefault(user_id, assignment_id)

    repo = EnrollmentRepository(session)
    upserted: list[UpsertedEnrollment] = []
    if major is not None:
        students = list(covered.items())
        for start in range(0, len(students), CHUNK):
            chunk = students[start : start + CHUNK]
            rows = await repo.upsert_active(
                [
                    {"organization_id": organization_id, "user_id": user_id,
                     "course_id": course_id, "major_version": major,
                     "source_assignment_id": assignment_id}
                    for user_id, assignment_id in chunk
                ]
            )  # fmt: skip
            upserted.extend(rows)
            for row in rows:
                if row.created:
                    events.enrollment_created(
                        session, enrollment_id=row.id, organization_id=organization_id,
                        user_id=row.user_id, course_id=course_id, major_version=row.major_version,
                        assignment_id=covered[row.user_id],
                    )  # fmt: skip
    revoked = await repo.revoke(organization_id, course_id=course_id, keep_user_ids=list(covered))
    result = ReconcileResult(
        enrolled=sum(r.created for r in upserted),
        reactivated=sum(not r.created for r in upserted),
        revoked=len(revoked),
    )
    logger.info(
        "enrollments_reconciled", course_id=str(course_id), organization_id=str(organization_id),
        enrolled=result.enrolled, reactivated=result.reactivated, revoked=result.revoked,
    )  # fmt: skip
    return result


async def reconcile_student(
    session: AsyncSession, organization_id: UUID, user_id: UUID
) -> ReconcileResult:
    """Make one student's enrollments in an org match the courses assigned to their batches
    (after they join or leave a batch or the org). System job."""
    batch_ids = await identity.student_batch_ids(session, organization_id, user_id)
    assigned = await courses.courses_for_batches(session, organization_id, batch_ids)
    majors = await courses.latest_majors(session, list(assigned))
    repo = EnrollmentRepository(session)
    rows = await repo.upsert_active(
        [
            {"organization_id": organization_id, "user_id": user_id, "course_id": course_id,
             "major_version": majors[course_id], "source_assignment_id": assignment_id}
            for course_id, assignment_id in assigned.items()
            if course_id in majors
        ]
    )  # fmt: skip
    _emit_created(session, organization_id, rows, assigned)
    revoked = await repo.revoke(organization_id, user_id=user_id, keep_course_ids=list(assigned))
    return ReconcileResult(
        enrolled=sum(r.created for r in rows),
        reactivated=sum(not r.created for r in rows),
        revoked=len(revoked),
    )


# ============================================================================ student APIs


async def _outs(ctx: Ctx, enrollments: Sequence[Enrollment]) -> list[EnrollmentOut]:
    titles = await courses.course_titles(ctx.session, [e.course_id for e in enrollments])
    versions = await courses.resolve_versions(
        ctx.session, [(e.course_id, e.major_version) for e in enrollments], redis=ctx.redis
    )
    return [
        EnrollmentOut(
            id=e.id,
            course_id=e.course_id,
            course_title=titles.get(e.course_id, ""),
            major_version=e.major_version,
            version=v.label if (v := versions.get((e.course_id, e.major_version))) else None,
            status=e.status,  # type: ignore[arg-type]
            progress_percent=e.progress_percent,
            last_lesson_id=e.last_lesson_id,
            last_accessed_at=e.last_accessed_at,
            completed_at=e.completed_at,
            enrolled_at=e.enrolled_at,
        )
        for e in enrollments
    ]


def _progress_out(p: LessonProgress) -> LessonProgressOut:
    return LessonProgressOut(
        lesson_id=p.lesson_id,
        status=p.status,  # type: ignore[arg-type]
        video_position_seconds=p.video_position_seconds,
        watched_ratio=p.watched_ratio,
        pdf_opened_at=p.pdf_opened_at,
        completed_at=p.completed_at,
    )


async def list_my_enrollments(
    ctx: Ctx, params: CursorParams, *, course_id: UUID | None = None
) -> tuple[list[EnrollmentOut], str | None]:
    org_id = require_org(ctx.principal)
    rows, cursor = await EnrollmentRepository(ctx.session).list_for_user(
        ctx.principal.user_id, org_id, params, course_id=course_id
    )
    return await _outs(ctx, rows), cursor


async def _my_enrollment(ctx: Ctx, enrollment_id: UUID) -> tuple[Enrollment, VersionRef]:
    """The caller's own active enrollment and the version it shows (404 otherwise, including
    when the course's assignment was removed and RLS now hides the version)."""
    require_org(ctx.principal)
    enrollment = await EnrollmentRepository(ctx.session).get(enrollment_id)
    if (
        enrollment is None
        or enrollment.user_id != ctx.principal.user_id
        or enrollment.status != EnrollmentStatus.ACTIVE
    ):
        raise NotFoundError("Enrollment not found.")
    version = await courses.resolve_version(
        ctx.session, enrollment.course_id, enrollment.major_version, redis=ctx.redis
    )
    if version is None:
        raise NotFoundError("Enrollment not found.")
    return enrollment, version


async def get_enrollment(ctx: Ctx, enrollment_id: UUID) -> EnrollmentDetail:
    enrollment, version = await _my_enrollment(ctx, enrollment_id)
    lessons = await courses.version_lessons_many(ctx.session, [version.id], redis=ctx.redis)
    await LessonProgressRepository(ctx.session).reset_replaced_videos(
        enrollment_id,
        {
            lesson.lesson_id: lesson.video_asset_id
            for lesson in lessons[version.id]
            if lesson.video_asset_id is not None
        },
    )
    progress = await LessonProgressRepository(ctx.session).for_enrollment(enrollment_id)
    return EnrollmentDetail(
        enrollment=(await _outs(ctx, [enrollment]))[0],
        version=EnrollmentVersion(
            id=version.id, major=version.major, minor=version.minor, version=version.label,
            title=version.title,
        ),
        outline=version.snapshot,
        progress=[_progress_out(p) for p in progress],
    )  # fmt: skip


async def _lesson_in_version(ctx: Ctx, version: VersionRef, lesson_id: UUID) -> VersionLessonRef:
    lesson = await courses.version_lesson(ctx.session, version.id, lesson_id, redis=ctx.redis)
    if lesson is None:
        raise NotFoundError("Lesson not found.")
    return lesson


async def open_pdf(
    ctx: Ctx, storage: ObjectStorage, enrollment_id: UUID, lesson_id: UUID, ttl_seconds: int
) -> FileDownloadOut:
    """A short-lived signed URL for a pdf lesson's file. Issuing it counts as opening the PDF,
    which the lesson's completion rule requires."""
    enrollment, version = await _my_enrollment(ctx, enrollment_id)
    lesson = await _lesson_in_version(ctx, version, lesson_id)
    if lesson.lesson_type != "pdf" or not lesson.file_ids:
        raise NotFoundError("PDF lesson not found.")
    file_id = lesson.file_ids[0]
    download = (await media.download_urls(ctx.session, storage, [file_id], ttl_seconds)).get(
        file_id
    )
    if download is None:
        raise NotFoundError("PDF not found.")
    await LessonProgressRepository(ctx.session).mark_pdf_opened(
        enrollment, lesson_id, datetime.now(UTC)
    )
    return FileDownloadOut(
        url=download.url, file_name=download.file_name, expires_at=download.expires_at
    )


async def lesson_images(
    ctx: Ctx, storage: ObjectStorage, enrollment_id: UUID, lesson_id: UUID, ttl_seconds: int
) -> LessonImagesOut:
    """Signed URLs for the images in a notes lesson's pre-rendered HTML."""
    _, version = await _my_enrollment(ctx, enrollment_id)
    lesson = await _lesson_in_version(ctx, version, lesson_id)
    if lesson.lesson_type != "notes":
        raise NotFoundError("Notes lesson not found.")
    found = await media.download_urls(ctx.session, storage, lesson.file_ids, ttl_seconds)
    return LessonImagesOut(
        urls={file_id: d.url for file_id, d in found.items()},
        expires_at=min((d.expires_at for d in found.values()), default=None),
    )


async def visit_lesson(ctx: Ctx, enrollment_id: UUID, lesson_id: UUID) -> EnrollmentOut:
    """The student opened a lesson: drives "Continue learning" and resume."""
    enrollment, version = await _my_enrollment(ctx, enrollment_id)
    await _lesson_in_version(ctx, version, lesson_id)
    await LessonProgressRepository(ctx.session).start(enrollment, lesson_id)
    repo = EnrollmentRepository(ctx.session)
    await repo.touch(enrollment_id, lesson_id)
    refreshed = await repo.get(enrollment_id)
    assert refreshed is not None  # noqa: S101
    return (await _outs(ctx, [refreshed]))[0]


async def complete_lesson(ctx: Ctx, enrollment_id: UUID, lesson_id: UUID) -> LessonCompletionOut:
    """Mark a notes or PDF lesson complete (videos complete by being watched)."""
    enrollment, version = await _my_enrollment(ctx, enrollment_id)
    lesson = await _lesson_in_version(ctx, version, lesson_id)
    rule = completion_rule(lesson.lesson_type)
    if rule is CompletionRule.NOT_COMPLETABLE:
        raise ConflictError("This lesson type can't be completed yet.", code="not_completable")
    if rule is CompletionRule.WATCHED:
        raise ConflictError(
            "Videos complete automatically once watched.", code="completed_by_watching"
        )
    if rule is CompletionRule.PASSED:
        raise ConflictError("Quizzes complete when an attempt passes.", code="completed_by_quiz")
    if rule is CompletionRule.GRADED:
        raise ConflictError(
            "Assignments complete once they are graded.", code="completed_by_grading"
        )
    if rule is CompletionRule.MANUAL_AFTER_OPENING:
        existing = await LessonProgressRepository(ctx.session).get(enrollment_id, lesson_id)
        if existing is None or existing.pdf_opened_at is None:
            raise ConflictError("Open the PDF before marking it complete.", code="pdf_not_opened")
    await record_completion(ctx.session, enrollment, version, lesson)
    await EnrollmentRepository(ctx.session).touch(enrollment_id, lesson_id)
    refreshed = await EnrollmentRepository(ctx.session).get(enrollment_id)
    progress = await LessonProgressRepository(ctx.session).get(enrollment_id, lesson_id)
    assert refreshed is not None  # noqa: S101
    assert progress is not None  # noqa: S101
    return LessonCompletionOut(
        enrollment=(await _outs(ctx, [refreshed]))[0], lesson=_progress_out(progress)
    )


@dataclass(frozen=True, slots=True)
class StudentLesson:
    """The caller's own active enrollment, the version it shows, and one lesson in it."""

    enrollment_id: UUID
    organization_id: UUID
    user_id: UUID
    course_id: UUID
    version: VersionRef
    lesson: VersionLessonRef


async def student_lesson(ctx: Ctx, enrollment_id: UUID, lesson_id: UUID) -> StudentLesson:
    """For other modules' student APIs (assignments): 404 unless the caller is the enrolled
    student and the lesson is in the version they're shown."""
    enrollment, version = await _my_enrollment(ctx, enrollment_id)
    lesson = await _lesson_in_version(ctx, version, lesson_id)
    return StudentLesson(
        enrollment.id, enrollment.organization_id, enrollment.user_id, enrollment.course_id,
        version, lesson,
    )  # fmt: skip


async def complete_graded_lesson(
    session: AsyncSession, enrollment_id: UUID, lesson_id: UUID, *, redis: Redis | None = None
) -> int | None:
    """An assignment was graded: complete its lesson and recompute progress, in the grader's
    transaction (RLS lets a grader write progress only where a graded submission exists,
    migration 0011). Returns the progress percentage, or None when the enrollment isn't active
    or its current version no longer has the lesson (a later major dropped it)."""
    enrollment = await EnrollmentRepository(session).get(enrollment_id)
    if enrollment is None or enrollment.status != EnrollmentStatus.ACTIVE:
        return None
    version = await courses.resolve_version(
        session, enrollment.course_id, enrollment.major_version, redis=redis
    )
    if version is None:
        return None
    lesson = await courses.version_lesson(session, version.id, lesson_id, redis=redis)
    if lesson is None or completion_rule(lesson.lesson_type) is not CompletionRule.GRADED:
        return None
    return await record_completion(session, enrollment, version, lesson)


async def record_completion(
    session: AsyncSession, enrollment: Enrollment, version: VersionRef, lesson: VersionLessonRef
) -> int:
    """Complete a lesson and recompute the course percentage in the same transaction. Emits
    `lesson_completed` once per lesson. Also used by the video heartbeat flush. Returns the
    enrollment's progress percentage."""
    await EnrollmentRepository(session).lock_many([enrollment.id])
    newly = await LessonProgressRepository(session).complete(
        enrollment, lesson.lesson_id, datetime.now(UTC)
    )
    percent = await recompute_progress(session, [enrollment.id], version.id)
    if newly:
        events.lesson_completed(
            session, enrollment_id=enrollment.id, organization_id=enrollment.organization_id,
            user_id=enrollment.user_id, course_id=enrollment.course_id,
            lesson_id=lesson.lesson_id, lesson_type=lesson.lesson_type.value,
            version_id=version.id, progress_percent=percent[enrollment.id],
        )  # fmt: skip
    return percent[enrollment.id]


async def recompute_progress(
    session: AsyncSession, enrollment_ids: Sequence[UUID], version_id: UUID
) -> dict[UUID, int]:
    """Recompute and store progress for enrollments that all show `version_id`."""
    await EnrollmentRepository(session).lock_many(enrollment_ids)
    version = await courses.version_ref(session, version_id)
    if version is None:
        raise NotFoundError("Course version not found.")
    required = await courses.required_lesson_ids(session, version_id)
    lessons = (await courses.version_lessons_many(session, [version_id]))[version_id]
    evidence: set[tuple[UUID, UUID]] = set()
    for kind in (LessonType.QUIZ, LessonType.ASSIGNMENT):
        ids = [lesson.lesson_id for lesson in lessons if lesson.lesson_type == kind]
        if ids:
            evidence |= await completion_sources.get(kind).evidence(
                session, enrollment_ids, ids, major=version.major
            )
    newly = await LessonProgressRepository(session).complete_evidence(evidence)
    done = await LessonProgressRepository(session).completed_counts(enrollment_ids, required)
    percent = {eid: course_percent(done[eid], len(required)) for eid in enrollment_ids}
    await EnrollmentRepository(session).set_progress(percent)
    if newly:
        owners = {
            e.id: e
            for e in await EnrollmentRepository(session).lock_many(list({eid for eid, _ in newly}))
        }
        by_lesson = {lesson.lesson_id: lesson for lesson in lessons}
        for eid, lid in newly:
            e = owners[eid]
            events.lesson_completed(
                session,
                enrollment_id=eid,
                organization_id=e.organization_id,
                user_id=e.user_id,
                course_id=e.course_id,
                lesson_id=lid,
                lesson_type=by_lesson[lid].lesson_type.value,
                version_id=version_id,
                progress_percent=percent[eid],
            )
    return percent


# ============================================================================ watched videos


async def complete_watched_video(
    session: AsyncSession, enrollment_id: UUID, lesson_id: UUID
) -> int | None:
    """Complete a video lesson whose stored watch progress meets its threshold, with the same
    rule the heartbeat flush applies (`video_watched`). Nothing happens otherwise. Returns the
    course progress percentage, or None when the lesson isn't watched enough."""
    enrollment = await EnrollmentRepository(session).get(enrollment_id)
    if enrollment is None:
        raise NotFoundError("Enrollment not found.")
    version = await courses.resolve_version(session, enrollment.course_id, enrollment.major_version)
    lesson = await courses.version_lesson(session, version.id, lesson_id) if version else None
    if version is None or lesson is None or lesson.video_asset_id is None:
        raise NotFoundError("Video lesson not found.")
    progress = await LessonProgressRepository(session).get(enrollment_id, lesson_id)
    ratio = progress.watched_ratio if progress else None
    if progress is None or progress.video_asset_id != lesson.video_asset_id:
        return None
    if not video_watched(ratio, lesson.completion_threshold):
        return None
    return await record_completion(session, enrollment, version, lesson)


# ============================================================================ reports interface
# For the progress report (reports module). Staff read their org's rows through RLS.


@dataclass(frozen=True, slots=True)
class EnrollmentProgress:
    enrollment_id: UUID
    user_id: UUID
    status: str
    major_version: int
    progress_percent: int
    last_accessed_at: datetime | None
    completed_at: datetime | None
    lessons: dict[UUID, str]  # lesson id -> not_started | in_progress | completed


async def course_progress_for_users(
    session: AsyncSession, course_id: UUID, user_ids: Sequence[UUID]
) -> dict[UUID, EnrollmentProgress]:
    """Each user's enrollment in the course with per-lesson status, in two queries."""
    enrollments = await EnrollmentRepository(session).for_course_users(course_id, user_ids)
    rows = await LessonProgressRepository(session).for_enrollments([e.id for e in enrollments])
    lessons: dict[UUID, dict[UUID, str]] = {e.id: {} for e in enrollments}
    for row in rows:
        lessons[row.enrollment_id][row.lesson_id] = row.status
    return {
        e.user_id: EnrollmentProgress(
            e.id,
            e.user_id,
            e.status,
            e.major_version,
            e.progress_percent,
            e.last_accessed_at,
            e.completed_at,
            lessons[e.id],
        )
        for e in enrollments
    }


async def course_summaries_for_users(
    session: AsyncSession, course_ids: Sequence[UUID], user_ids: Sequence[UUID]
) -> dict[UUID, tuple[int, int, float]]:
    """Per course: (active enrollments, completed, average progress %) among these users."""
    return await EnrollmentRepository(session).summaries(course_ids, user_ids)


# ============================================================================ platform counts

# Asia/Kolkata has been UTC+05:30 without DST since 1945, so a fixed offset is exact and needs no
# tz database (slim images and Windows hosts don't ship one).
INDIA = timezone(timedelta(hours=5, minutes=30), "IST")


def start_of_day_ist(now: datetime) -> datetime:
    """Midnight today in India, as a UTC instant (the platform's "today")."""
    local = now.astimezone(INDIA)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def enrollment_counts(session: AsyncSession, now: datetime) -> EnrollmentCounts:
    """Platform dashboard counts. The caller must be a platform admin (RLS shows all rows).
    "Active today" is learning activity (a lesson opened) since midnight IST; it deliberately
    doesn't use `users.last_login_at`, which nothing writes."""
    since = start_of_day_ist(now)
    repo = EnrollmentRepository(session)
    return EnrollmentCounts(
        enrollments=await repo.count_by_status(),
        active_today=await repo.active_users_since(since),
        active_since=since,
    )


# ============================================================================ major opt-in


async def request_upgrade(ctx: Ctx, course_id: UUID, data: UpgradeRequest) -> UpgradeAccepted:
    """An org_admin opts their org's enrollments (all, or those of chosen batches) into a newer
    major version. Runs in the background; RLS limits it to the admin's own org."""
    org_id = require_org_permission(ctx.principal, Permission.ENROLLMENT_UPGRADE)
    await courses.readable_course(ctx, course_id)
    if await courses.resolve_version(ctx.session, course_id, data.to_major) is None:
        raise UnprocessableError("That version doesn't exist.", code="unknown_version")
    found = await identity.batch_ids_in_org(ctx.session, org_id, data.batch_ids)
    if missing := [b for b in data.batch_ids if b not in found]:
        raise UnprocessableError(
            "Batches must belong to your organization.",
            code="invalid_batch",
            details={"batch_ids": missing},
        )
    enrollment_jobs.enqueue_upgrade(
        ctx.session, ctx.jobs, course_id=course_id, organization_id=org_id,
        user_id=ctx.principal.user_id, to_major=data.to_major, batch_ids=data.batch_ids,
    )  # fmt: skip
    out = UpgradeAccepted(course_id=course_id, to_major=data.to_major, batch_ids=data.batch_ids)
    await audit.record(
        ctx.session, ctx.actor, action="enrollments.upgrade_requested", target_type="course",
        target_id=course_id, after=out,
    )  # fmt: skip
    return out


async def run_upgrade(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    course_id: UUID,
    organization_id: UUID,
    user_id: UUID,
    to_major: int,
    batch_ids: Sequence[UUID],
    chunk_size: int = CHUNK,
) -> int:
    """Move matching enrollments to `to_major` in chunks (one transaction each), carrying over
    completions of lessons that still exist and recomputing percentages. Acts as the requesting
    org_admin, so RLS confines it to their org. Safe to re-run. Returns how many moved."""

    async def begin(session: AsyncSession) -> None:
        await set_tenant_context(session, organization_id=organization_id, user_id=user_id)

    async with sessionmaker() as session, session.begin():
        await begin(session)
        version = await courses.resolve_version(session, course_id, to_major)
        if version is None:
            return 0
        user_ids: list[UUID] | None = None
        if batch_ids:
            user_ids = []
            for batch_id in batch_ids:
                async for page in identity.iter_batch_student_ids(session, batch_id):
                    user_ids.extend(page)
            if not user_ids:
                return 0

    moved, after = 0, None
    while True:
        async with sessionmaker() as session, session.begin():
            await begin(session)
            repo = EnrollmentRepository(session)
            batch = await repo.upgrade_candidates(
                course_id, organization_id, below_major=to_major, user_ids=user_ids,
                after=after, limit=chunk_size,
            )  # fmt: skip
            if not batch:
                break
            ids = [c.id for c in batch]
            await repo.set_major(ids, to_major)
            await completion_sources.get(LessonType.QUIZ).close_major(session, ids, to_major)
            await completion_sources.get(LessonType.ASSIGNMENT).close_major(session, ids, to_major)
            percent = await recompute_progress(session, ids, version.id)
            for c in batch:
                events.enrollment_version_changed(
                    session, enrollment_id=c.id, organization_id=organization_id,
                    user_id=c.user_id, course_id=course_id, from_major=c.major_version,
                    to_major=to_major, progress_percent=percent[c.id], actor_user_id=user_id,
                )  # fmt: skip
            moved += len(batch)
            after = ids[-1]
    logger.info(
        "enrollments_upgraded", course_id=str(course_id), organization_id=str(organization_id),
        to_major=to_major, moved=moved,
    )  # fmt: skip
    return moved


async def complete_passed_quiz(
    session: AsyncSession, enrollment_id: UUID, lesson_id: UUID, major: int
) -> int | None:
    """Called only through assessments; require durable pass evidence and current batch access."""
    enrollment = await EnrollmentRepository(session).get(enrollment_id)
    if (
        enrollment is None
        or enrollment.status != EnrollmentStatus.ACTIVE
        or enrollment.major_version != major
    ):
        return None
    if not await courses.student_has_course(
        session, enrollment.user_id, enrollment.organization_id, enrollment.course_id
    ):
        return None
    source = completion_sources.get(LessonType.QUIZ)
    if (enrollment_id, lesson_id) not in await source.evidence(
        session, [enrollment_id], [lesson_id], major=major
    ):
        return None
    version = await courses.resolve_version(session, enrollment.course_id, major)
    lesson = await courses.version_lesson(session, version.id, lesson_id) if version else None
    if version is None or lesson is None or lesson.lesson_type != LessonType.QUIZ:
        return None
    return await record_completion(session, enrollment, version, lesson)
