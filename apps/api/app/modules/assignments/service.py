"""Assignments public interface.

- **Authoring** (owner-org editors): one definition per assignment lesson, edited with the course
  revision as `If-Match`. Publishing copies it into the version snapshot through the courses
  module's content-source hook (`AssignmentContentSource`, connected in `app.wiring`).
- **Students**: one active submission per assignment (text or a file upload); resubmitting
  replaces it until it is graded. `If-Match` is the submission revision (`0` for the first).
- **Graders** (`instructor`, `org_admin` of the student's org): a queue per lesson, ungraded
  first; grading records the score and feedback, audits it, and completes the lesson in the same
  transaction.

Deliberately not here yet (later phases): rubrics, late policy (due dates are shown, never
enforced), attempt history, plagiarism checks, AI feedback.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
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
    SubmissionKind,
    SubmissionStatus,
)
from app.modules.assignments.repository import (
    AssignmentRepository,
    GradeRepository,
    SubmissionRepository,
)
from app.modules.assignments.schemas import (
    MAX_INSTRUCTIONS_BYTES,
    AssignmentDraftOut,
    AssignmentUpsert,
    FileUploadOut,
    GradeBody,
    GradeOut,
    GraderSubmissionDetail,
    GraderSubmissionRow,
    PublishedAssignment,
    StudentAssignmentOut,
    StudentSummary,
    SubmissionFileOut,
    SubmissionOut,
    SubmissionUploadCreate,
    SubmitBody,
    SubmitText,
)
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
    if courses.notes_image_ids(doc):
        raise UnprocessableError(
            "Assignment instructions can't contain images yet.", code="instructions_images"
        )
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
    lesson = await _assignment_lesson(ctx, course_id, lesson_id)
    _check_instructions(body.instructions)
    repo = AssignmentRepository(ctx.session)
    before = await repo.by_lesson(lesson_id)
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
        before=_draft_out(before, lesson.course_revision) if before else None, after=out,
    )  # fmt: skip
    return out


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
    )


def _grade_out(grade: AssignmentGrade) -> GradeOut:
    return GradeOut(
        score=grade.score, max_marks=grade.max_marks, feedback=grade.feedback,
        graded_at=grade.graded_at, graded_by=grade.graded_by,
    )  # fmt: skip


async def get_student_assignment(
    ctx: Ctx, storage: ObjectStorage, settings: Settings, enrollment_id: UUID, lesson_id: UUID
) -> StudentAssignmentOut:
    _, assignment = await _student_assignment(ctx, enrollment_id, lesson_id)
    submission = await SubmissionRepository(ctx.session).for_enrollment(
        enrollment_id, assignment.assignment_id
    )
    if submission is None:
        return StudentAssignmentOut(assignment=assignment, submission=None)
    grades = await GradeRepository(ctx.session).for_submissions([submission.id])
    return StudentAssignmentOut(
        assignment=assignment,
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
) -> FileUploadOut:
    """A presigned upload for a file submission (then `PUT .../submission` with its id)."""
    student, assignment = await _student_assignment(ctx, enrollment_id, lesson_id)
    _require_kind(assignment, SubmissionKind.FILE)
    existing = await SubmissionRepository(ctx.session).for_enrollment(
        student.enrollment_id, assignment.assignment_id
    )
    if existing is not None and existing.status == SubmissionStatus.GRADED:
        raise ConflictError("This assignment has already been graded.", code="already_graded")
    return await media.create_submission_upload(
        ctx, storage, settings, file_name=body.file_name, content_type=body.content_type
    )


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
    student, assignment = await _student_assignment(ctx, enrollment_id, lesson_id)
    entry = body.submission
    _require_kind(assignment, entry.kind)
    repo = SubmissionRepository(ctx.session)
    existing = await repo.for_enrollment(student.enrollment_id, assignment.assignment_id)
    if existing is not None and existing.status == SubmissionStatus.GRADED:
        raise ConflictError("This assignment has already been graded.", code="already_graded")
    current = existing.revision if existing else 0
    if if_match != current:
        raise ConflictError(
            "Your submission changed in another tab or device. Reload and try again.",
            code="revision_conflict",
            details={"current_revision": current},
        )

    if isinstance(entry, SubmitText):
        content: dict[str, Any] = {
            "kind": SubmissionKind.TEXT,
            "text_body": entry.text,
            "file_id": None,
        }
    else:
        file = await media.confirm_submission_file(ctx, storage, settings, entry.file_id)
        content = {"kind": SubmissionKind.FILE, "text_body": None, "file_id": file.id}

    values = {**content, "version_id": student.version.id, "submitted_at": datetime.now(UTC)}
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
        updated = await repo.update(existing.id, current, values)
        if updated is None:  # another request resubmitted in between
            raise ConflictError(
                "Your submission changed. Reload and try again.", code="revision_conflict"
            )
        submission = updated
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
                kind=r.kind,  # type: ignore[arg-type]
                status=r.status,  # type: ignore[arg-type]
                revision=r.revision,
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
    if submission.user_id == ctx.principal.user_id:
        raise ConflictError("You can't grade your own submission.", code="cannot_grade_own")
    if if_match != submission.revision:
        raise ConflictError(
            "The submission changed since you opened it. Reload and grade again.",
            code="revision_conflict",
            details={"current_revision": submission.revision},
        )
    assignment = await _version_assignment(ctx, submission)
    score = body.score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if score > assignment.max_marks:
        raise UnprocessableError(
            f"The score can't be more than {assignment.max_marks}.",
            code="score_out_of_range",
            details={"max_marks": assignment.max_marks},
        )
    grades = GradeRepository(ctx.session)
    previous = (await grades.for_submissions([submission.id])).get(submission.id)
    before = _grade_out(previous).model_dump(mode="json") if previous else None
    row = await grades.upsert(
        {
            "organization_id": submission.organization_id,
            "submission_id": submission.id,
            "user_id": submission.user_id,
            "score": score,
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
        ctx.session, updated, score=score, max_marks=assignment.max_marks,
        graded_by=ctx.principal.user_id, regrade=previous is not None,
    )  # fmt: skip
    # Completion needs the grade row first: RLS lets graders write progress only then.
    await enrollments.complete_graded_lesson(
        ctx.session, submission.enrollment_id, submission.lesson_id, redis=ctx.redis
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
        rows = await SubmissionRepository(session).for_enrollments(enrollment_ids, lesson_ids)
        grades = await GradeRepository(session).for_submissions([s.id for s in rows])
        return {(s.enrollment_id, s.lesson_id) for s in rows if s.id in grades}

    async def close_major(
        self, session: AsyncSession, enrollment_ids: Sequence[UUID], major: int
    ) -> None:
        # Assignment aggregates retain their established behavior; history arrives in step 4.
        return None
