"""Assignment tables (migrations 0011 and 0015).

`assignments` is draft content of the course **owner** org (editors only, like lessons); students
and graders read the published copy in the version snapshot. Submissions and grades belong to the
**student's** org. Immutable attempts retain every accepted submission; the stable submission
aggregate projects one active attempt, and append-only grades retain its grading history.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SubmissionKind(StrEnum):
    FILE = "file"
    TEXT = "text"


class SubmissionStatus(StrEnum):
    SUBMITTED = "submitted"
    GRADED = "graded"


class Assignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "organization_id"],
            ["courses.id", "courses.organization_id"],
            ondelete="CASCADE",
            name="fk_assignments_course_id_courses",
        ),
        UniqueConstraint("lesson_id", name="uq_assignments_lesson_id"),
        CheckConstraint("max_marks BETWEEN 1 AND 1000", name="ck_assignments_max_marks"),
        CheckConstraint(
            "cardinality(submission_kinds) >= 1 "
            "AND submission_kinds <@ ARRAY['file', 'text']::varchar[]",
            name="ck_assignments_submission_kinds",
        ),
        Index("ix_assignments_course_org", "course_id", "organization_id"),
        Index("ix_assignments_organization_id", "organization_id"),
        Index("ix_assignments_created_by", "created_by"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    course_id: Mapped[UUID] = mapped_column(Uuid)
    lesson_id: Mapped[UUID] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    instructions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    due_at: Mapped[datetime | None]
    max_marks: Mapped[int] = mapped_column(Integer)
    submission_kinds: Mapped[list[str]] = mapped_column(ARRAY(String(10)))
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    rubric: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    late_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AssignmentSubmission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A student's one active submission (replaced on resubmit until graded)."""

    __tablename__ = "assignment_submissions"
    __table_args__ = (
        Index(
            "ix_assignment_submissions_org_queue", "organization_id", "status", "submitted_at", "id"
        ),
        UniqueConstraint(
            "enrollment_id",
            "assignment_id",
            name="uq_assignment_submissions_enrollment_assignment",
        ),
        UniqueConstraint(
            "id", "organization_id", name="uq_assignment_submissions_id_organization_id"
        ),
        CheckConstraint(
            "status IN ('submitted', 'graded')", name="ck_assignment_submissions_status"
        ),
        CheckConstraint(
            "(kind = 'text' AND text_body IS NOT NULL AND file_id IS NULL) "
            "OR (kind = 'file' AND file_id IS NOT NULL AND text_body IS NULL)",
            name="ck_assignment_submissions_content",
        ),
        CheckConstraint(
            "text_body IS NULL OR char_length(text_body) <= 20000",
            name="ck_assignment_submissions_text_length",
        ),
        # The graders' list: an org's submissions for one lesson, ungraded first, oldest first.
        Index(
            "ix_assignment_submissions_queue",
            "organization_id",
            "lesson_id",
            "status",
            "submitted_at",
            "id",
        ),
        Index("ix_assignment_submissions_enrollment_lesson", "enrollment_id", "lesson_id"),
        Index("ix_assignment_submissions_course_id", "course_id"),
        Index("ix_assignment_submissions_version_id", "version_id"),
        Index("ix_assignment_submissions_user_id", "user_id"),
        Index("ix_assignment_submissions_file_id", "file_id"),
        ForeignKeyConstraint(
            ["active_attempt_id", "id", "organization_id"],
            [
                "submission_attempts.id",
                "submission_attempts.submission_id",
                "submission_attempts.organization_id",
            ],
            name="fk_assignment_submissions_active_attempt",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_assignment_submissions_active_attempt_id", "active_attempt_id"),
        Index(
            "ix_assignment_submissions_active_attempt_org",
            "active_attempt_id",
            "id",
            "organization_id",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    # Not foreign keys: the draft rows may be deleted while the published version remains.
    assignment_id: Mapped[UUID] = mapped_column(Uuid)
    lesson_id: Mapped[UUID] = mapped_column(Uuid)
    course_id: Mapped[UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("course_versions.id"))
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))
    text_body: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[UUID | None] = mapped_column(ForeignKey("files.id"))
    status: Mapped[str] = mapped_column(String(20), server_default=SubmissionStatus.SUBMITTED)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    submitted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    active_attempt_id: Mapped[UUID | None] = mapped_column(Uuid)


class SubmissionAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "submission_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["submission_id", "organization_id"],
            ["assignment_submissions.id", "assignment_submissions.organization_id"],
            ondelete="CASCADE",
            name="fk_submission_attempts_submission",
        ),
        UniqueConstraint("submission_id", "attempt_number", name="uq_submission_attempts_number"),
        UniqueConstraint(
            "id", "submission_id", "organization_id", name="uq_submission_attempts_identity"
        ),
        CheckConstraint("attempt_number >= 1", name="ck_submission_attempts_number"),
        CheckConstraint("major_version >= 1", name="ck_submission_attempts_major_version"),
        CheckConstraint(
            "(kind = 'text' AND text_body IS NOT NULL AND file_id IS NULL) OR "
            "(kind = 'file' AND file_id IS NOT NULL AND text_body IS NULL)",
            name="ck_submission_attempts_content",
        ),
        CheckConstraint(
            "text_body IS NULL OR char_length(text_body) <= 20000",
            name="ck_submission_attempts_text_length",
        ),
        Index("ix_submission_attempts_organization_id", "organization_id"),
        Index("ix_submission_attempts_submission_org", "submission_id", "organization_id"),
        Index("ix_submission_attempts_user_id", "user_id"),
        Index("ix_submission_attempts_version_id", "version_id"),
        Index("ix_submission_attempts_file_id", "file_id"),
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    submission_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    major_version: Mapped[int] = mapped_column(Integer)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("course_versions.id"))
    kind: Mapped[str] = mapped_column(String(10))
    text_body: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[UUID | None] = mapped_column(ForeignKey("files.id"))
    submitted_at: Mapped[datetime]
    assignment_rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    late_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AssignmentGrade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assignment_grades"
    __table_args__ = (
        Index(
            "ix_assignment_grades_org_user_recent", "organization_id", "user_id", "graded_at", "id"
        ),
        ForeignKeyConstraint(
            ["submission_id", "organization_id"],
            ["assignment_submissions.id", "assignment_submissions.organization_id"],
            ondelete="CASCADE",
            name="fk_assignment_grades_submission",
        ),
        UniqueConstraint(
            "attempt_id", "grade_sequence", name="uq_assignment_grades_attempt_sequence"
        ),
        ForeignKeyConstraint(
            ["attempt_id", "submission_id", "organization_id"],
            [
                "submission_attempts.id",
                "submission_attempts.submission_id",
                "submission_attempts.organization_id",
            ],
            name="fk_assignment_grades_attempt",
        ),
        CheckConstraint("grade_sequence >= 1", name="ck_assignment_grades_sequence"),
        CheckConstraint(
            "raw_score >= 0 AND raw_score <= max_marks AND penalty_percent BETWEEN 0 AND 100 "
            "AND penalty_marks >= 0 AND score = greatest(0, raw_score - penalty_marks)",
            name="ck_assignment_grades_penalty",
        ),
        Index("ix_assignment_grades_submission_id", "submission_id"),
        Index("ix_assignment_grades_submission_org", "submission_id", "organization_id"),
        Index("ix_assignment_grades_attempt_org", "attempt_id", "submission_id", "organization_id"),
        CheckConstraint("score >= 0 AND score <= max_marks", name="ck_assignment_grades_score"),
        CheckConstraint(
            "char_length(feedback) <= 10000", name="ck_assignment_grades_feedback_length"
        ),
        Index("ix_assignment_grades_organization_id", "organization_id"),
        Index("ix_assignment_grades_user_id", "user_id"),
        Index("ix_assignment_grades_graded_by", "graded_by"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    submission_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    score: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    # The version's max marks when graded (a minor release can't change them: structural).
    max_marks: Mapped[int] = mapped_column(Integer)
    feedback: Mapped[str] = mapped_column(Text, server_default="")
    graded_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    graded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    attempt_id: Mapped[UUID] = mapped_column(Uuid)
    grade_sequence: Mapped[int] = mapped_column(Integer)
    rubric_breakdown: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    raw_score: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    penalty_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    penalty_marks: Mapped[Decimal] = mapped_column(Numeric(7, 2))
