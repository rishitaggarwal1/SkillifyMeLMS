"""Assignments public interface.

- **Authoring** (owner-org editors): one definition per assignment lesson, edited with the course
  revision as `If-Match`. Publishing copies it into the version snapshot through the courses
  module's content-source hook (`AssignmentContentSource`, connected in `app.wiring`).
- **Students**: one active submission per assignment (text or a file upload); resubmitting
  replaces it until it is graded. `If-Match` is the submission revision (`0` for the first).
- **Graders** (`instructor`, `org_admin` of the student's org): a queue per lesson, ungraded
  first; grading records the score and feedback, audits it, and completes the lesson in the same
  transaction.

Attempts and grades are immutable history; rubric and late rules freeze at acceptance.
Plagiarism checks and AI feedback remain follow-ups.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    InvalidCursorError,
    NotFoundError,
    UnprocessableError,
)
from app.core.pagination import CursorParams, decode_cursor, encode_cursor
from app.core.storage import ObjectStorage
from app.db.base import new_id
from app.modules.assignments import events
from app.modules.assignments.models import (
    Assignment,
    AssignmentGrade,
    AssignmentSubmission,
    SubmissionAttempt,
    SubmissionKind,
    SubmissionStatus,
)
from app.modules.assignments.repository import (
    AssignmentRepository,
    AttemptRepository,
    GradeRepository,
    SubmissionRepository,
)
from app.modules.assignments.schemas import (
    MAX_INSTRUCTIONS_BYTES,
    AssignmentDraftOut,
    AssignmentPreviewOut,
    AssignmentUpsert,
    CriterionScore,
    FileUploadOut,
    GradeBody,
    GradeOut,
    GraderSubmissionDetail,
    GraderSubmissionRow,
    LateData,
    LatePolicy,
    PublishedAssignment,
    Rubric,
    StudentAssignmentOut,
    StudentSummary,
    SubmissionAttemptOut,
    SubmissionFileOut,
    SubmissionOut,
    SubmissionUploadCreate,
    SubmitBody,
    SubmitText,
)
from app.modules.assignments.scoring import grade_values, lateness
from app.modules.audit import service as audit
from app.modules.courses import service as courses
from app.modules.courses.models import LessonType
from app.modules.courses.schemas import StructuralChange
from app.modules.enrollments import service as enrollments
from app.modules.identity import service as identity
from app.modules.identity.authz import Permission, require_org_permission
from app.modules.identity.dependencies import RequestContext
from app.modules.media import service as media

Ctx = RequestContext


# ============================================================================ publishing hook


def published_content(row: Assignment) -> dict[str, Any]:
    """What a version stores for an assignment lesson (JSON). Instructions are rendered to
    sanitized HTML once, at publish, exactly like notes."""
    return {
        "assignment_id": str(row.id),
        "title": row.title,
        "instructions_html": courses.render_notes(row.instructions) if row.instructions else "",
        "due_at": row.due_at.isoformat() if row.due_at else None,
        "max_marks": row.max_marks,
        "submission_kinds": sorted(row.submission_kinds),
        "rubric": row.rubric,
        "late_policy": row.late_policy or {"mode": "accept", "percent_per_day": None},
        "image_file_ids": [str(i) for i in courses.notes_image_ids(row.instructions)]
        if row.instructions
        else [],
    }


class AssignmentContentSource:
    """Publishes assignment lessons; a lesson without a definition isn't ready."""

    async def lock(self, session: AsyncSession, lesson_ids: Sequence[UUID]) -> None:
        pass

    async def structural(
        self, session: AsyncSession, lesson_ids: Sequence[UUID]
    ) -> dict[UUID, dict[str, Any]]:
        return {}

    async def changes(
        self, session: AsyncSession, previous_version_id: UUID, lesson_ids: Sequence[UUID]
    ) -> list[StructuralChange]:
        return []

    async def publish(
        self, session: AsyncSession, version_id: UUID, major: int, lesson_ids: Sequence[UUID]
    ) -> None:
        pass

    async def published(
        self, session: AsyncSession, lesson_ids: Sequence[UUID]
    ) -> dict[UUID, dict[str, Any]]:
        rows = await AssignmentRepository(session).by_lessons(lesson_ids)
        return {row.lesson_id: published_content(row) for row in rows}


def _published(content: dict[str, Any] | None) -> PublishedAssignment:
    """The published assignment from a snapshot lesson's content (404 if the version predates
    assignments: those lessons were placeholders)."""
    if not content or "assignment_id" not in content:
        raise NotFoundError("This assignment isn't available.")
    return PublishedAssignment.model_validate(content)


# ============================================================================ authoring


def _draft_out(row: Assignment, course_revision: int) -> AssignmentDraftOut:
    return AssignmentDraftOut(
        id=row.id, course_id=row.course_id, lesson_id=row.lesson_id, title=row.title,
        instructions=row.instructions, due_at=row.due_at, max_marks=row.max_marks,
        submission_kinds=sorted(row.submission_kinds),  # type: ignore[arg-type]
        course_revision=course_revision, updated_at=row.updated_at,
        rubric=Rubric.model_validate(row.rubric) if row.rubric else None,
        late_policy=LatePolicy.model_validate(row.late_policy) if row.late_policy else None,
    )  # fmt: skip


async def _assignment_lesson(ctx: Ctx, course_id: UUID, lesson_id: UUID) -> courses.DraftLessonRef:
    lesson = await courses.editable_lesson(ctx, course_id, lesson_id)
    if lesson.lesson_type != LessonType.ASSIGNMENT:
        raise NotFoundError("Assignment lesson not found.")
    return lesson


def _check_instructions(doc: dict[str, Any] | None) -> None:
    if doc is None:
        return
    try:
        courses.validate_notes_doc(doc)
    except ValueError as exc:
        raise UnprocessableError(
            "The instructions aren't a valid document.", code="invalid_instructions",
            details={"reason": str(exc)},
        ) from exc  # fmt: skip
    if len(json.dumps(doc)) > MAX_INSTRUCTIONS_BYTES:
        raise UnprocessableError("The instructions are too long.", code="content_too_large")


async def get_draft(ctx: Ctx, course_id: UUID, lesson_id: UUID) -> AssignmentDraftOut | None:
    """The lesson's assignment definition, or null before it is first saved."""
    lesson = await _assignment_lesson(ctx, course_id, lesson_id)
    row = await AssignmentRepository(ctx.session).by_lesson(lesson_id)
    return _draft_out(row, lesson.course_revision) if row else None


async def put_draft(
    ctx: Ctx, course_id: UUID, lesson_id: UUID, body: AssignmentUpsert, if_match: int
) -> AssignmentDraftOut:
    await courses.lock_outline(ctx, course_id, if_match)
    lesson = await _assignment_lesson(ctx, course_id, lesson_id)
    _check_instructions(body.instructions)
    repo = AssignmentRepository(ctx.session)
    before = await repo.by_lesson(lesson_id)
    before_out = _draft_out(before, lesson.course_revision) if before else None
    rubric = (
        body.rubric
        if "rubric" in body.model_fields_set
        else (Rubric.model_validate(before.rubric) if before and before.rubric else None)
    )
    policy = (
        body.late_policy
        if "late_policy" in body.model_fields_set
        else (
            LatePolicy.model_validate(before.late_policy) if before and before.late_policy else None
        )
    )
    if rubric and sum((c.max_marks for c in rubric.criteria), Decimal(0)) != body.max_marks:
        raise UnprocessableError("Criterion maximums must sum to max_marks.", code="invalid_rubric")
    if policy and policy.mode == "penalty" and body.due_at is None:
        raise UnprocessableError(
            "A penalty policy requires a due date.", code="late_policy_due_required"
        )
    image_ids = courses.notes_image_ids(body.instructions) if body.instructions else []
    images = await media.files(ctx.session, image_ids)
    if any(
        (image := images.get(i)) is None
        or image.organization_id != lesson.organization_id
        or image.kind != "image"
        or not image.is_ready
        for i in image_ids
    ):
        raise UnprocessableError(
            "Use confirmed images of the course owner organization.", code="invalid_image"
        )
    row = await repo.upsert(
        {
            "organization_id": lesson.organization_id,
            "course_id": lesson.course_id,
            "lesson_id": lesson_id,
            "title": body.title,
            "instructions": body.instructions,
            "due_at": body.due_at,
            "max_marks": body.max_marks,
            "submission_kinds": body.submission_kinds,
            "created_by": ctx.principal.user_id,
            "rubric": rubric.model_dump(mode="json") if rubric else None,
            "late_policy": policy.model_dump(mode="json") if policy else None,
        }
    )
    # Bumps the course revision (If-Match; 409 when stale, which rolls the upsert back too).
    revision = await courses.edit_lesson_content(
        ctx, course_id, lesson_id, {"assignment_id": str(row.id)}, if_match
    )
    out = _draft_out(row, revision)
    await audit.record(
        ctx.session, ctx.actor, action="assignment.saved", target_type="assignment",
        target_id=row.id,
        before=before_out, after=out,
    )  # fmt: skip
    return out


async def preview_draft(
    ctx: Ctx, storage: ObjectStorage, settings: Settings, course_id: UUID, lesson_id: UUID
) -> AssignmentPreviewOut:
    await _assignment_lesson(ctx, course_id, lesson_id)
    row = await AssignmentRepository(ctx.session).by_lesson(lesson_id)
    if row is None:
        raise NotFoundError("Assignment not found.")
    urls = await media.download_urls(
        ctx.session,
        storage,
        courses.notes_image_ids(row.instructions) if row.instructions else [],
        settings.file_download_ttl_seconds,
    )
    return AssignmentPreviewOut(
        html=courses.render_notes(row.instructions) if row.instructions else "",
        image_urls={i: u.url for i, u in urls.items()},
        expires_at=min((u.expires_at for u in urls.values()), default=None),
    )


# ============================================================================ students


async def _student_assignment(
    ctx: Ctx, enrollment_id: UUID, lesson_id: UUID
) -> tuple[enrollments.StudentLesson, PublishedAssignment]:
    student = await enrollments.student_lesson(ctx, enrollment_id, lesson_id)
    if student.lesson.lesson_type != LessonType.ASSIGNMENT:
        raise NotFoundError("Assignment lesson not found.")
    snapshot = courses.snapshot_lesson(student.version, lesson_id)
    return student, _published(snapshot.get("content") if snapshot else None)


async def _submission_out(
    ctx: Ctx,
    storage: ObjectStorage,
    settings: Settings,
    submission: AssignmentSubmission,
    grade: AssignmentGrade | None,
) -> SubmissionOut:
    file: SubmissionFileOut | None = None
    if submission.file_id is not None:
        info = (await media.files(ctx.session, [submission.file_id])).get(submission.file_id)
        urls = await media.download_urls(
            ctx.session, storage, [submission.file_id], settings.file_download_ttl_seconds
        )
        signed = urls.get(submission.file_id)
        file = SubmissionFileOut(
            id=submission.file_id,
            file_name=info.file_name if info else "",
            content_type=info.content_type if info else "",
            url=signed.url if signed else None,
            expires_at=signed.expires_at if signed else None,
        )
    return SubmissionOut(
        id=submission.id,
        kind=submission.kind,  # type: ignore[arg-type]
        text_body=submission.text_body,
        file=file,
        status=submission.status,  # type: ignore[arg-type]
        revision=submission.revision,
        submitted_at=submission.submitted_at,
        grade=_grade_out(grade) if grade else None,
        active_attempt_id=submission.active_attempt_id,
        attempt_number=attempt.attempt_number
        if (attempt := await AttemptRepository(ctx.session).get(submission.active_attempt_id))
        else None,
        late=LateData.model_validate(attempt.late_data) if attempt and attempt.late_data else None,
    )


def _grade_out(grade: AssignmentGrade) -> GradeOut:
    return GradeOut(
        score=grade.score, max_marks=grade.max_marks, feedback=grade.feedback,
        graded_at=grade.graded_at, graded_by=grade.graded_by,
        id=grade.id, attempt_id=grade.attempt_id, grade_sequence=grade.grade_sequence,
        rubric_breakdown=(
            [CriterionScore.model_validate(c) for c in grade.rubric_breakdown]
            if grade.rubric_breakdown is not None else None), raw_score=grade.raw_score,
        penalty_percent=grade.penalty_percent, penalty_marks=grade.penalty_marks,
    )  # fmt: skip


async def get_student_assignment(
    ctx: Ctx, storage: ObjectStorage, settings: Settings, enrollment_id: UUID, lesson_id: UUID
) -> StudentAssignmentOut:
    _, assignment = await _student_assignment(ctx, enrollment_id, lesson_id)
    submission = await SubmissionRepository(ctx.session).for_enrollment(
        enrollment_id, assignment.assignment_id
    )
    now = await SubmissionRepository(ctx.session).database_now()
    if submission is None:
        return StudentAssignmentOut(
            assignment=assignment, submission=None, server_time=now, late=lateness(assignment, now)
        )
    grades = await GradeRepository(ctx.session).for_submissions([submission.id])
    return StudentAssignmentOut(
        assignment=assignment,
        server_time=now,
        late=lateness(assignment, now),
        submission=await _submission_out(
            ctx, storage, settings, submission, grades.get(submission.id)
        ),
    )


def _require_kind(assignment: PublishedAssignment, kind: str) -> None:
    if kind not in assignment.submission_kinds:
        raise UnprocessableError(
            "This assignment doesn't accept that kind of submission.",
            code="submission_kind_not_allowed",
            details={"allowed": assignment.submission_kinds},
        )


async def create_submission_upload(
    ctx: Ctx,
    storage: ObjectStorage,
    settings: Settings,
    *,
    enrollment_id: UUID,
    lesson_id: UUID,
    body: SubmissionUploadCreate,
    if_match: int,
) -> FileUploadOut:
    """A presigned upload for a file submission (then `PUT .../submission` with its id)."""
    await enrollments.lock_assessment_enrollment(ctx.session, enrollment_id)
    student, assignment = await _student_assignment(ctx, enrollment_id, lesson_id)
    _require_kind(assignment, SubmissionKind.FILE)
    existing = await SubmissionRepository(ctx.session).for_enrollment(
        student.enrollment_id, assignment.assignment_id
    )
    if existing is not None and existing.status == SubmissionStatus.GRADED:
        raise ConflictError("This assignment has already been graded.", code="already_graded")
    _check_revision(existing, if_match)
    if lateness(assignment, await SubmissionRepository(ctx.session).database_now()).closed:
        raise ConflictError("This assignment is closed.", code="assignment_closed")
    return await media.create_submission_upload(
        ctx, storage, settings, file_name=body.file_name, content_type=body.content_type
    )


def _check_revision(existing: AssignmentSubmission | None, if_match: int) -> int:
    current = existing.revision if existing else 0
    if if_match != current:
        raise ConflictError(
            "Your submission changed. Reload and try again.",
            code="revision_conflict",
            details={"current_revision": current},
        )
    return current


async def submit(
    ctx: Ctx,
    storage: ObjectStorage,
    settings: Settings,
    *,
    enrollment_id: UUID,
    lesson_id: UUID,
    body: SubmitBody,
    if_match: int,
) -> SubmissionOut:
    await enrollments.lock_assessment_enrollment(ctx.session, enrollment_id)
    student, assignment = await _student_assignment(ctx, enrollment_id, lesson_id)
    entry = body.submission
    _require_kind(assignment, entry.kind)
    repo = SubmissionRepository(ctx.session)
    existing = await repo.for_enrollment(student.enrollment_id, assignment.assignment_id)
    if existing is not None and existing.status == SubmissionStatus.GRADED:
        raise ConflictError("This assignment has already been graded.", code="already_graded")
    if existing:
        existing = await repo.lock(existing.id)
    current = _check_revision(existing, if_match)

    if isinstance(entry, SubmitText):
        content: dict[str, Any] = {
            "kind": SubmissionKind.TEXT,
            "text_body": entry.text,
            "file_id": None,
        }
    else:
        file = await media.confirm_submission_file(ctx, storage, settings, entry.file_id)
        content = {"kind": SubmissionKind.FILE, "text_body": None, "file_id": file.id}

    accepted_at = await repo.database_now()
    late = lateness(assignment, accepted_at)
    if late.closed:
        raise ConflictError("This assignment is closed.", code="assignment_closed")
    aid = new_id()
    previous_attempt = (
        await AttemptRepository(ctx.session).get(existing.active_attempt_id) if existing else None
    )
    values = {**content, "version_id": student.version.id, "submitted_at": accepted_at}
    if existing is None:
        submission = await repo.create(
            AssignmentSubmission(
                id=new_id(), organization_id=student.organization_id,
                assignment_id=assignment.assignment_id, course_id=student.course_id,
                lesson_id=lesson_id, enrollment_id=student.enrollment_id, user_id=student.user_id,
                status=SubmissionStatus.SUBMITTED, revision=1, **values,
            )
        )  # fmt: skip
    else:
        updated = await repo.update(existing.id, current, {**values, "active_attempt_id": aid})
        if updated is None:  # another request resubmitted in between
            raise ConflictError(
                "Your submission changed. Reload and try again.", code="revision_conflict"
            )
        submission = updated
    await AttemptRepository(ctx.session).create(
        SubmissionAttempt(
            id=aid,
            submission_id=submission.id,
            organization_id=submission.organization_id,
            user_id=submission.user_id,
            attempt_number=previous_attempt.attempt_number + 1 if previous_attempt else 1,
            version_id=student.version.id,
            major_version=student.version.major,
            submitted_at=accepted_at,
            **content,
            assignment_rules=assignment.model_dump(mode="json"),
            late_data=late.model_dump(mode="json"),
        )
    )
    if existing is None:
        submission = await repo.bind_first_attempt(submission.id, aid)
    events.assignment_submitted(ctx.session, submission, resubmission=existing is not None)
    return await _submission_out(ctx, storage, settings, submission, None)


# ============================================================================ reports interface


@dataclass(frozen=True, slots=True)
class SubmissionState:
    status: str  # submitted | graded
    score: Decimal | None
    max_marks: int | None


async def submission_states(
    session: AsyncSession, enrollment_ids: Sequence[UUID], lesson_ids: Sequence[UUID]
) -> dict[tuple[UUID, UUID], SubmissionState]:
    """(enrollment, lesson) -> submission status and grade, in two queries (progress reports)."""
    rows = await SubmissionRepository(session).for_enrollments(enrollment_ids, lesson_ids)
    grades = await GradeRepository(session).for_submissions([r.id for r in rows])
    result: dict[tuple[UUID, UUID], SubmissionState] = {}
    for r in rows:
        g = grades.get(r.id)
        result[(r.enrollment_id, r.lesson_id)] = SubmissionState(
            r.status, g.score if g else None, g.max_marks if g else None
        )
    return result


# ============================================================================ graders


_RANK = {SubmissionStatus.SUBMITTED: 0, SubmissionStatus.GRADED: 1}


def _student(users: dict[UUID, Any], user_id: UUID) -> StudentSummary:
    user = users.get(user_id)
    return StudentSummary(
        id=user_id, full_name=user.full_name if user else "", email=user.email if user else ""
    )


async def list_submissions(
    ctx: Ctx,
    course_id: UUID,
    lesson_id: UUID,
    params: CursorParams,
    *,
    status: str | None,
    batch_id: UUID | None,
) -> tuple[list[GraderSubmissionRow], str | None]:
    """The org's submissions for one assignment lesson: ungraded first, then oldest first."""
    org_id = require_org_permission(ctx.principal, Permission.ASSIGNMENT_GRADE)
    await courses.readable_course(ctx, course_id)
    user_ids: list[UUID] | None = None
    if batch_id is not None:
        if not await identity.batch_belongs_to_org(ctx.session, batch_id, org_id):
            raise NotFoundError("Batch not found.")
        user_ids = await identity.batch_student_ids(ctx.session, batch_id)
    after: tuple[int, datetime, UUID] | None = None
    if params.cursor:
        data = decode_cursor(params.cursor)
        try:
            after = (int(data["r"]), datetime.fromisoformat(str(data["t"])), UUID(str(data["id"])))
        except (KeyError, ValueError) as exc:
            raise InvalidCursorError from exc
    rows = await SubmissionRepository(ctx.session).queue(
        org_id, lesson_id, status=status, user_ids=user_ids, after=after, limit=params.limit + 1
    )
    page, more = rows[: params.limit], len(rows) > params.limit
    rows = [r for r in page if r.course_id == course_id]
    grades = await GradeRepository(ctx.session).for_submissions([r.id for r in rows])
    users = await identity.user_summaries(ctx.session, [r.user_id for r in rows])
    items = []
    for r in rows:
        g = grades.get(r.id)
        items.append(
            GraderSubmissionRow(
                id=r.id,
                student=_student(users, r.user_id),
                enrollment_id=r.enrollment_id,
                kind=r.kind,  # type: ignore[arg-type]  # type: ignore[arg-type]
                status=r.status,  # type: ignore[arg-type]
                revision=r.revision,
                active_attempt_id=r.active_attempt_id,
                submitted_at=r.submitted_at,
                score=g.score if g else None,
                max_marks=g.max_marks if g else None,
            )
        )
    cursor = None
    if more:
        last = page[-1]
        cursor = encode_cursor(
            {
                "r": _RANK[SubmissionStatus(last.status)],
                "t": last.submitted_at.isoformat(),
                "id": str(last.id),
            }
        )
    return items, cursor


async def _graders_submission(ctx: Ctx, submission_id: UUID) -> AssignmentSubmission:
    org_id = require_org_permission(ctx.principal, Permission.ASSIGNMENT_GRADE)
    submission = await SubmissionRepository(ctx.session).get(submission_id)
    if submission is None or submission.organization_id != org_id:
        raise NotFoundError("Submission not found.")
    return submission


async def _version_assignment(ctx: Ctx, submission: AssignmentSubmission) -> PublishedAssignment:
    attempt = await AttemptRepository(ctx.session).get(submission.active_attempt_id)
    if attempt:
        return _published(attempt.assignment_rules)
    version = await courses.version_ref(ctx.session, submission.version_id, redis=ctx.redis)
    snapshot = courses.snapshot_lesson(version, submission.lesson_id) if version else None
    return _published(snapshot.get("content") if snapshot else None)


async def _detail(
    ctx: Ctx, storage: ObjectStorage, settings: Settings, submission: AssignmentSubmission
) -> GraderSubmissionDetail:
    grades = await GradeRepository(ctx.session).for_submissions([submission.id])
    users = await identity.user_summaries(ctx.session, [submission.user_id])
    return GraderSubmissionDetail(
        id=submission.id,
        course_id=submission.course_id,
        lesson_id=submission.lesson_id,
        student=_student(users, submission.user_id),
        assignment=await _version_assignment(ctx, submission),
        submission=await _submission_out(
            ctx, storage, settings, submission, grades.get(submission.id)
        ),
    )


async def get_submission(
    ctx: Ctx, storage: ObjectStorage, settings: Settings, submission_id: UUID
) -> GraderSubmissionDetail:
    return await _detail(ctx, storage, settings, await _graders_submission(ctx, submission_id))


async def grade(
    ctx: Ctx,
    storage: ObjectStorage,
    settings: Settings,
    *,
    submission_id: UUID,
    body: GradeBody,
    if_match: int,
) -> GraderSubmissionDetail:
    """Record (or correct) the grade, audit it, and complete the lesson, in one transaction."""
    submission = await _graders_submission(ctx, submission_id)
    await enrollments.lock_assessment_enrollment(ctx.session, submission.enrollment_id)
    locked = await SubmissionRepository(ctx.session).lock(submission_id)
    if locked is None:
        raise NotFoundError("Submission not found.")
    submission = locked
    if submission.user_id == ctx.principal.user_id:
        raise ConflictError("You can't grade your own submission.", code="cannot_grade_own")
    if if_match != submission.revision:
        raise ConflictError(
            "The submission changed since you opened it. Reload and grade again.",
            code="revision_conflict",
            details={"current_revision": submission.revision},
        )
    assignment = await _version_assignment(ctx, submission)
    if body.attempt_id is not None and body.attempt_id != submission.active_attempt_id:
        raise ConflictError("Only the active attempt can be graded.", code="inactive_attempt")
    attempt = await AttemptRepository(ctx.session).get(submission.active_attempt_id)
    if attempt is None:
        raise NotFoundError("Attempt not found.")
    late = LateData.model_validate(attempt.late_data) if attempt.late_data else None
    scores = grade_values(assignment, late, body)
    grades = GradeRepository(ctx.session)
    previous = (await grades.for_submissions([submission.id])).get(submission.id)
    before = _grade_out(previous).model_dump(mode="json") if previous else None
    row = await grades.upsert(
        {
            "organization_id": submission.organization_id,
            "submission_id": submission.id,
            "user_id": submission.user_id,
            **scores,
            "attempt_id": attempt.id,
            "grade_sequence": previous.grade_sequence + 1 if previous else 1,
            "max_marks": assignment.max_marks,
            "feedback": body.feedback,
            "graded_by": ctx.principal.user_id,
        }
    )
    updated = await SubmissionRepository(ctx.session).update(
        submission.id, submission.revision, {"status": SubmissionStatus.GRADED}
    )
    if updated is None:
        raise ConflictError(
            "The submission changed. Reload and grade again.", code="revision_conflict"
        )
    await audit.record(
        ctx.session, ctx.actor, action="assignment.graded", target_type="assignment_submission",
        target_id=submission.id, before=before, after=_grade_out(row).model_dump(mode="json"),
    )  # fmt: skip
    events.assignment_graded(
        ctx.session, updated, grade=row, attempt_number=attempt.attempt_number,
        late=late, regrade=previous is not None,
    )  # fmt: skip
    # Completion needs the grade row first: RLS lets graders write progress only then.
    await enrollments.complete_graded_lesson(
        ctx.session,
        submission.enrollment_id,
        submission.lesson_id,
        redis=ctx.redis,
        major=attempt.major_version,
    )
    return await _detail(ctx, storage, settings, updated)


class AssignmentCompletionSource:
    async def evidence(
        self,
        session: AsyncSession,
        enrollment_ids: Sequence[UUID],
        lesson_ids: Sequence[UUID],
        *,
        major: int,
    ) -> set[tuple[UUID, UUID]]:
        return await AttemptRepository(session).graded_evidence(enrollment_ids, lesson_ids, major)

    async def close_major(
        self, session: AsyncSession, enrollment_ids: Sequence[UUID], major: int
    ) -> None:
        # Keep historical work; completion evidence is scoped to the displayed major.
        return None


# ============================================================================ immutable history


def _history_after(params: CursorParams) -> UUID | None:
    if not params.cursor:
        return None
    try:
        return UUID(str(decode_cursor(params.cursor)["id"]))
    except (KeyError, ValueError) as exc:
        raise InvalidCursorError from exc


async def _attempt_rows(
    ctx: Ctx,
    storage: ObjectStorage,
    settings: Settings,
    submission: AssignmentSubmission,
    rows: list[SubmissionAttempt],
) -> list[SubmissionAttemptOut]:
    grades = await GradeRepository(ctx.session).for_attempts([r.id for r in rows])
    definitions = {r.id: _published(r.assignment_rules) for r in rows}
    file_ids = list(
        {r.file_id for r in rows if r.file_id is not None}
        | {i for d in definitions.values() for i in d.image_file_ids}
    )
    files = await media.files(ctx.session, file_ids)
    urls = await media.download_urls(
        ctx.session, storage, file_ids, settings.file_download_ttl_seconds
    )
    items = []
    for r in rows:
        file = None
        if r.file_id is not None:
            info, signed = files.get(r.file_id), urls.get(r.file_id)
            file = SubmissionFileOut(
                id=r.file_id,
                file_name=info.file_name if info else "",
                content_type=info.content_type if info else "",
                url=signed.url if signed else None,
                expires_at=signed.expires_at if signed else None,
            )
        definition = definitions[r.id]
        images = {i: urls[i] for i in definition.image_file_ids if i in urls}
        items.append(
            SubmissionAttemptOut(
                id=r.id,
                submission_id=r.submission_id,
                attempt_number=r.attempt_number,
                version_id=r.version_id,
                submitted_at=r.submitted_at,
                is_active=r.id == submission.active_attempt_id,
                kind=r.kind,  # type: ignore[arg-type]
                text_body=r.text_body,
                file=file,
                assignment=definition,
                late=LateData.model_validate(r.late_data) if r.late_data else None,
                grade=_grade_out(grades[r.id]) if r.id in grades else None,
                image_urls={i: u.url for i, u in images.items()},
                image_urls_expires_at=min((u.expires_at for u in images.values()), default=None),
            )
        )
    return items


async def _attempt_history(
    ctx: Ctx,
    storage: ObjectStorage,
    settings: Settings,
    submission: AssignmentSubmission,
    params: CursorParams,
) -> tuple[list[SubmissionAttemptOut], str | None]:
    rows = await AttemptRepository(ctx.session).history(
        submission.id, _history_after(params), params.limit + 1
    )
    page = rows[: params.limit]
    cursor = encode_cursor({"id": str(page[-1].id)}) if len(rows) > params.limit else None
    return await _attempt_rows(ctx, storage, settings, submission, page), cursor


async def student_attempt_history(
    ctx: Ctx,
    storage: ObjectStorage,
    settings: Settings,
    enrollment_id: UUID,
    lesson_id: UUID,
    *,
    params: CursorParams,
) -> tuple[list[SubmissionAttemptOut], str | None]:
    _, assignment = await _student_assignment(ctx, enrollment_id, lesson_id)
    submission = await SubmissionRepository(ctx.session).for_enrollment(
        enrollment_id, assignment.assignment_id
    )
    if submission is None:
        return [], None
    return await _attempt_history(ctx, storage, settings, submission, params)


async def grader_attempt_history(
    ctx: Ctx, storage: ObjectStorage, settings: Settings, submission_id: UUID, params: CursorParams
) -> tuple[list[SubmissionAttemptOut], str | None]:
    submission = await _graders_submission(ctx, submission_id)
    return await _attempt_history(ctx, storage, settings, submission, params)


async def _attempt(
    ctx: Ctx, submission: AssignmentSubmission, attempt_id: UUID
) -> SubmissionAttempt:
    row = await AttemptRepository(ctx.session).get(attempt_id)
    if row is None or row.submission_id != submission.id:
        raise NotFoundError("Attempt not found.")
    return row


async def grader_attempt_detail(
    ctx: Ctx, storage: ObjectStorage, settings: Settings, submission_id: UUID, attempt_id: UUID
) -> SubmissionAttemptOut:
    submission = await _graders_submission(ctx, submission_id)
    row = await _attempt(ctx, submission, attempt_id)
    return (await _attempt_rows(ctx, storage, settings, submission, [row]))[0]


async def _grade_history(
    ctx: Ctx, attempt: SubmissionAttempt, params: CursorParams
) -> tuple[list[GradeOut], str | None]:
    rows = await GradeRepository(ctx.session).history(
        attempt.id, _history_after(params), params.limit + 1
    )
    page = rows[: params.limit]
    cursor = encode_cursor({"id": str(page[-1].id)}) if len(rows) > params.limit else None
    return [_grade_out(g) for g in page], cursor


async def grader_grade_history(
    ctx: Ctx, submission_id: UUID, attempt_id: UUID, params: CursorParams
) -> tuple[list[GradeOut], str | None]:
    return await _grade_history(
        ctx, await _attempt(ctx, await _graders_submission(ctx, submission_id), attempt_id), params
    )


async def student_grade_history(
    ctx: Ctx, enrollment_id: UUID, lesson_id: UUID, attempt_id: UUID, params: CursorParams
) -> tuple[list[GradeOut], str | None]:
    _, assignment = await _student_assignment(ctx, enrollment_id, lesson_id)
    submission = await SubmissionRepository(ctx.session).for_enrollment(
        enrollment_id, assignment.assignment_id
    )
    if submission is None:
        raise NotFoundError("Attempt not found.")
    return await _grade_history(ctx, await _attempt(ctx, submission, attempt_id), params)
