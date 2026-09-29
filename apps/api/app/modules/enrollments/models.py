"""Enrollments and per-lesson progress. `organization_id` is the **student's** org."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EnrollmentStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"  # assignment removed; progress kept so re-assignment restores it


class LessonProgressStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class Enrollment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A student's enrollment pins a **major** version; they see its latest minor."""

    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("user_id", "course_id", name="uq_enrollments_user_course"),
        CheckConstraint(
            "progress_percent BETWEEN 0 AND 100", name="ck_enrollments_progress_percent"
        ),
        CheckConstraint("major_version >= 1", name="ck_enrollments_major_version"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_enrollments_status"),
        Index("ix_enrollments_org_course", "organization_id", "course_id"),
        Index("ix_enrollments_course_id", "course_id"),
        # "Continue learning": a student's most recently used enrollments.
        Index("ix_enrollments_user_last_accessed", "user_id", "last_accessed_at"),
        Index("ix_enrollments_source_assignment_id", "source_assignment_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    major_version: Mapped[int] = mapped_column(Integer)
    source_assignment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("course_assignments.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), server_default=EnrollmentStatus.ACTIVE)
    progress_percent: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    last_lesson_id: Mapped[UUID | None] = mapped_column(Uuid)
    last_accessed_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    enrolled_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LessonProgress(Base):
    __tablename__ = "lesson_progress"
    __table_args__ = (
        CheckConstraint(
            "status IN ('not_started', 'in_progress', 'completed')",
            name="ck_lesson_progress_status",
        ),
        CheckConstraint(
            "watched_ratio IS NULL OR (watched_ratio >= 0 AND watched_ratio <= 1)",
            name="ck_lesson_progress_watched_ratio",
        ),
        Index("ix_lesson_progress_user_id", "user_id"),
        Index("ix_lesson_progress_organization_id", "organization_id"),
        Index("ix_lesson_progress_lesson_id", "lesson_id"),
    )

    enrollment_id: Mapped[UUID] = mapped_column(
        ForeignKey("enrollments.id", ondelete="CASCADE"), primary_key=True
    )
    lesson_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(20), server_default=LessonProgressStatus.NOT_STARTED)
    video_position_seconds: Mapped[int | None] = mapped_column(Integer)
    # Bitmap of watched 5-second segments; the asset it refers to (reset if the video changes).
    watched_segments: Mapped[bytes | None] = mapped_column(LargeBinary)
    watched_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    video_asset_id: Mapped[UUID | None] = mapped_column(Uuid)
    buffer_revision: Mapped[UUID | None] = mapped_column(Uuid)
    pdf_opened_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
