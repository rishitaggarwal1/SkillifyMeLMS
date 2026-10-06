"""Domain events of the assignments module (through the outbox; see docs/events.md). Keyed by
enrollment id, like the enrollments module's events, so a student's submission, grade and the
resulting `lesson_completed` are on one topic partition in order."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.outbox import add_outbox_event
from app.modules.assignments.models import AssignmentGrade, AssignmentSubmission
from app.modules.assignments.schemas import LateData

ENROLLMENT_AGGREGATE = "enrollment"
ASSIGNMENT_SUBMITTED = "assignment_submitted"
ASSIGNMENT_GRADED = "assignment_graded"
SCHEMA_VERSION = 1


def _base(s: AssignmentSubmission) -> dict[str, str]:
    return {
        "submission_id": str(s.id),
        "assignment_id": str(s.assignment_id),
        "enrollment_id": str(s.enrollment_id),
        "user_id": str(s.user_id),
        "course_id": str(s.course_id),
        "lesson_id": str(s.lesson_id),
        "version_id": str(s.version_id),
    }


def _emit(
    session: AsyncSession,
    event_type: str,
    s: AssignmentSubmission,
    data: dict[str, object],
    *,
    version: int = 1,
) -> None:
    add_outbox_event(
        session,
        aggregate_type=ENROLLMENT_AGGREGATE,
        aggregate_id=s.enrollment_id,
        event_type=event_type,
        organization_id=s.organization_id,
        payload={**_base(s), **data},
        headers={"version": version},
    )


def assignment_submitted(
    session: AsyncSession, submission: AssignmentSubmission, *, resubmission: bool
) -> None:
    _emit(
        session,
        ASSIGNMENT_SUBMITTED,
        submission,
        {"kind": submission.kind, "resubmission": resubmission},
    )


def assignment_graded(
    session: AsyncSession,
    submission: AssignmentSubmission,
    *,
    grade: AssignmentGrade,
    attempt_number: int,
    late: LateData | None,
    regrade: bool,
) -> None:
    _emit(
        session,
        ASSIGNMENT_GRADED,
        submission,
        {
            # A string keeps the exact decimal (JSON numbers are floats for most consumers).
            "score": f"{grade.score:.2f}",
            "max_marks": grade.max_marks,
            "graded_by": str(grade.graded_by),
            "regrade": regrade,
            "grade_id": str(grade.id),
            "attempt_id": str(grade.attempt_id),
            "attempt_number": attempt_number,
            "grade_sequence": grade.grade_sequence,
            "raw_score": f"{grade.raw_score:.2f}",
            "penalty_percent": f"{grade.penalty_percent:.2f}",
            "penalty_marks": f"{grade.penalty_marks:.2f}",
            "rubric_breakdown": grade.rubric_breakdown,
            "late": late.model_dump(mode="json") if late else None,
        },
        version=2,
    )
