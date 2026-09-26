"""Courses: the editable draft tree (courses > modules > lessons), immutable published versions,
and two-level assignments (org grants and batch assignments).

Ownership and visibility rules (CLAUDE.md, "Content ownership & sharing") are enforced by the RLS
policies in migration 0004. `organization_id` on every table here is the course's **owner** org.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LessonType(StrEnum):
    VIDEO = "video"
    NOTES = "notes"
    PDF = "pdf"
    # Placeholders: their content is built in later phases (assessments, labs, assignments).
    QUIZ = "quiz"
    LAB = "lab"
    ASSIGNMENT = "assignment"


PLACEHOLDER_LESSON_TYPES = frozenset({LessonType.QUIZ, LessonType.LAB, LessonType.ASSIGNMENT})

lesson_type_enum = Enum(
    LessonType,
    name="lesson_type",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,  # created by migration 0004
)


class CourseStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ReleaseType(StrEnum):
    MAJOR = "major"
    MINOR = "minor"


class Course(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "courses"
    __table_args__ = (
        # Composite-FK target so child tables can't point at another org's course.
        UniqueConstraint("id", "organization_id", name="uq_courses_id_organization_id"),
        UniqueConstraint("organization_id", "slug", name="uq_courses_org_slug"),
        Index("ix_courses_organization_id_status", "organization_id", "status"),
        Index("ix_courses_created_by", "created_by"),
        Index("ix_courses_current_version_id", "current_version_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, server_default="")
    status: Mapped[str] = mapped_column(String(20), server_default=CourseStatus.ACTIVE)
    is_public_catalog: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Bumped by every draft edit; clients send it back (If-Match) to detect concurrent edits.
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("course_versions.id", ondelete="SET NULL", use_alter=True)
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class CourseModule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "course_modules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "organization_id"],
            ["courses.id", "courses.organization_id"],
            ondelete="CASCADE",
            name="fk_course_modules_course_org",
        ),
        UniqueConstraint("id", "course_id", name="uq_course_modules_id_course_id"),
        UniqueConstraint(
            "course_id",
            "position",
            name="uq_course_modules_course_position",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_course_modules_course_org", "course_id", "organization_id"),
    )

    course_id: Mapped[UUID] = mapped_column(Uuid)
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer)


class Lesson(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A lesson's id is stable for its whole life (including moves between modules), so progress
    and version snapshots can refer to it."""

    __tablename__ = "lessons"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "organization_id"],
            ["courses.id", "courses.organization_id"],
            ondelete="CASCADE",
            name="fk_lessons_course_org",
        ),
        ForeignKeyConstraint(
            ["module_id", "course_id"],
            ["course_modules.id", "course_modules.course_id"],
            ondelete="CASCADE",
            name="fk_lessons_module_course",
        ),
        UniqueConstraint(
            "module_id",
            "position",
            name="uq_lessons_module_position",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "completion_threshold IS NULL "
            "OR (completion_threshold > 0 AND completion_threshold <= 1)",
            name="ck_lessons_completion_threshold",
        ),
        Index("ix_lessons_course_org", "course_id", "organization_id"),
        Index("ix_lessons_module_course", "module_id", "course_id"),
        Index("ix_lessons_created_by", "created_by"),
    )

    course_id: Mapped[UUID] = mapped_column(Uuid)
    module_id: Mapped[UUID] = mapped_column(Uuid)
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    lesson_type: Mapped[LessonType] = mapped_column(lesson_type_enum)
    title: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer)
    is_required: Mapped[bool] = mapped_column(Boolean, server_default=true())
    # Video: fraction of the video that must be watched (default 0.9 when NULL).
    completion_threshold: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class LessonSkill(Base):
    __tablename__ = "lesson_skills"
    __table_args__ = (
        Index("ix_lesson_skills_skill_id", "skill_id"),
        Index("ix_lesson_skills_organization_id", "organization_id"),
    )

    lesson_id: Mapped[UUID] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), primary_key=True
    )
    organization_id: Mapped[UUID] = mapped_column(Uuid)  # the lesson's owner org (RLS)


class CourseVersion(UUIDPrimaryKeyMixin, Base):
    """An immutable published snapshot. UPDATE/DELETE are revoked from the app role."""

    __tablename__ = "course_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "organization_id"],
            ["courses.id", "courses.organization_id"],
            ondelete="CASCADE",
            name="fk_course_versions_course_org",
        ),
        UniqueConstraint(
            "course_id", "major", "minor", name="uq_course_versions_course_major_minor"
        ),
        CheckConstraint("major >= 1 AND minor >= 0", name="ck_course_versions_numbers"),
        CheckConstraint(
            "(release_type = 'major' AND minor = 0) OR (release_type = 'minor' AND minor > 0)",
            name="ck_course_versions_release_type",
        ),
        Index("ix_course_versions_course_org", "course_id", "organization_id"),
        Index("ix_course_versions_published_by", "published_by"),
    )

    course_id: Mapped[UUID] = mapped_column(Uuid)
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    major: Mapped[int] = mapped_column(Integer)
    minor: Mapped[int] = mapped_column(Integer)
    release_type: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    release_notes: Mapped[str] = mapped_column(Text, server_default="")
    published_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    published_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CourseVersionLesson(Base):
    """One row per lesson per version, so progress is computed with SQL, not by parsing JSON."""

    __tablename__ = "course_version_lessons"
    __table_args__ = (
        Index("ix_course_version_lessons_lesson_id", "lesson_id"),
        Index("ix_course_version_lessons_course_id", "course_id"),
        Index("ix_course_version_lessons_organization_id", "organization_id"),
        Index("ix_course_version_lessons_video_asset_id", "video_asset_id"),
    )

    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("course_versions.id", ondelete="CASCADE"), primary_key=True
    )
    lesson_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)  # stable id, not an FK
    course_id: Mapped[UUID] = mapped_column(Uuid)
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    module_id: Mapped[UUID] = mapped_column(Uuid)
    module_position: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    lesson_type: Mapped[LessonType] = mapped_column(lesson_type_enum)
    is_required: Mapped[bool] = mapped_column(Boolean)
    completion_threshold: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    video_asset_id: Mapped[UUID | None] = mapped_column(Uuid)
    video_duration_seconds: Mapped[int | None] = mapped_column(Integer)


class CourseAssignment(UUIDPrimaryKeyMixin, Base):
    """Two-level assignment.

    - Org grant (`batch_id` NULL): entitles `organization_id`; visible to its org_admins and
      instructors, never to students by itself.
    - Batch assignment (`batch_id` set): the batch's students see the course.
    `assigned_by_org_id` is the owner org (publisher-made) or the receiving org (an org_admin
    narrowing a grant; then `parent_assignment_id` points at that grant and cascades with it).
    """

    __tablename__ = "course_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "owner_organization_id"],
            ["courses.id", "courses.organization_id"],
            ondelete="CASCADE",
            name="fk_course_assignments_course_owner",
        ),
        ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["batches.id", "batches.organization_id"],
            ondelete="CASCADE",
            name="fk_course_assignments_batch_org",
        ),
        UniqueConstraint(
            "course_id",
            "organization_id",
            "batch_id",
            name="uq_course_assignments_course_org_batch",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "assigned_by_org_id IN (owner_organization_id, organization_id)",
            name="ck_course_assignments_assigned_by",
        ),
        CheckConstraint(
            # Rows an assigned org creates for itself narrow a grant: batch + parent required.
            "assigned_by_org_id = owner_organization_id "
            "OR (batch_id IS NOT NULL AND parent_assignment_id IS NOT NULL)",
            name="ck_course_assignments_narrowing",
        ),
        CheckConstraint(
            "parent_assignment_id IS NULL OR batch_id IS NOT NULL",
            name="ck_course_assignments_parent_needs_batch",
        ),
        Index("ix_course_assignments_course_owner", "course_id", "owner_organization_id"),
        Index("ix_course_assignments_org_course", "organization_id", "course_id"),
        Index("ix_course_assignments_batch_org", "batch_id", "organization_id"),
        Index("ix_course_assignments_parent", "parent_assignment_id"),
        Index("ix_course_assignments_created_by", "created_by"),
        Index("ix_course_assignments_assigned_by_org_id", "assigned_by_org_id"),
    )

    course_id: Mapped[UUID] = mapped_column(Uuid)
    owner_organization_id: Mapped[UUID] = mapped_column(Uuid)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    batch_id: Mapped[UUID | None] = mapped_column(Uuid)
    assigned_by_org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    parent_assignment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("course_assignments.id", ondelete="CASCADE")
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CatalogEntry(Base):
    """Public catalog card for courses marked `is_public_catalog` (written on publish). Readable by
    anyone; holds only public fields."""

    __tablename__ = "catalog_entries"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_catalog_entries_slug"),
        Index("ix_catalog_entries_organization_id", "organization_id"),
        Index("ix_catalog_entries_version_id", "version_id"),
    )

    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[UUID] = mapped_column(Uuid)  # owner org
    version_id: Mapped[UUID] = mapped_column(ForeignKey("course_versions.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, server_default="")
    skill_names: Mapped[list[str]] = mapped_column(ARRAY(String(120)), server_default="{}")
    lesson_count: Mapped[int] = mapped_column(Integer, server_default="0")
    published_at: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
