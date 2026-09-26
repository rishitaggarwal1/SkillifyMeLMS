"""Phase 2 learning content: skills taxonomy, courses (draft tree, immutable versions,
two-level assignments, public catalog), enrollments and lesson progress, media (videos, files).

RLS (per operation) implements CLAUDE.md "Content ownership & sharing":
- editors = owner-org instructor/org_admin (and platform admins) - only they write courses and see
  drafts;
- readers of published versions = editors, plus users of an org the course is assigned to: its
  org_admins/instructors via any assignment, students only via a batch assignment for their batch;
- two-level assignment: publisher-made rows are created/removed by the owner's editors (other orgs
  only if the owner is a content publisher); a receiving org_admin can only add batch rows that
  narrow an existing org grant, and remove only rows their org created;
- skills are global: everyone reads, platform admins and content-publisher staff write.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.rls import (
    APP_ROLE,
    PLATFORM_ADMIN,
    enable_rls,
    policy,
)
from app.db.types import LTree

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LESSON_TYPE = postgresql.ENUM(
    "video", "notes", "pdf", "quiz", "lab", "assignment", name="lesson_type", create_type=False
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree")
    # quiz/lab/assignment are placeholders now, so later phases need no enum migration.
    op.execute(
        "CREATE TYPE lesson_type AS ENUM ('video', 'notes', 'pdf', 'quiz', 'lab', 'assignment')"
    )
    op.create_table(
        "skills",
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("path", LTree(), nullable=False),
        sa.Column("description", sa.String(length=500), server_default="", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["skills.id"],
            name=op.f("fk_skills_parent_id_skills"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skills")),
        sa.UniqueConstraint("path", name="uq_skills_path"),
    )
    op.create_index("ix_skills_parent_id", "skills", ["parent_id"], unique=False)
    op.create_index(
        "ix_skills_path_gist", "skills", ["path"], unique=False, postgresql_using="gist"
    )
    op.create_table(
        "courses",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column(
            "is_public_catalog", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_courses_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_courses_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_courses")),
        sa.UniqueConstraint("id", "organization_id", name="uq_courses_id_organization_id"),
        sa.UniqueConstraint("organization_id", "slug", name="uq_courses_org_slug"),
    )
    op.create_index("ix_courses_created_by", "courses", ["created_by"], unique=False)
    op.create_index(
        "ix_courses_current_version_id", "courses", ["current_version_id"], unique=False
    )
    op.create_index(
        "ix_courses_organization_id_status", "courses", ["organization_id", "status"], unique=False
    )
    op.create_table(
        "files",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'rejected')", name=op.f("ck_files_ck_files_status")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_files_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_files_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_files")),
        sa.UniqueConstraint("storage_key", name="uq_files_storage_key"),
    )
    op.create_index("ix_files_created_by", "files", ["created_by"], unique=False)
    op.create_index(
        "ix_files_organization_id_status", "files", ["organization_id", "status"], unique=False
    )
    op.create_table(
        "video_assets",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_video_id", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="created", nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('created', 'processing', 'ready', 'failed')",
            name=op.f("ck_video_assets_ck_video_assets_status"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_video_assets_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_video_assets_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_assets")),
        sa.UniqueConstraint("provider", "provider_video_id", name="uq_video_assets_provider_video"),
    )
    op.create_index("ix_video_assets_created_by", "video_assets", ["created_by"], unique=False)
    op.create_index(
        "ix_video_assets_organization_id_status",
        "video_assets",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_table(
        "course_assignments",
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("owner_organization_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_by_org_id", sa.Uuid(), nullable=False),
        sa.Column("parent_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "assigned_by_org_id = owner_organization_id "
            "OR (batch_id IS NOT NULL AND parent_assignment_id IS NOT NULL)",
            name=op.f("ck_course_assignments_ck_course_assignments_narrowing"),
        ),
        sa.CheckConstraint(
            "assigned_by_org_id IN (owner_organization_id, organization_id)",
            name=op.f("ck_course_assignments_ck_course_assignments_assigned_by"),
        ),
        sa.CheckConstraint(
            "parent_assignment_id IS NULL OR batch_id IS NOT NULL",
            name=op.f("ck_course_assignments_ck_course_assignments_parent_needs_batch"),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_org_id"],
            ["organizations.id"],
            name=op.f("fk_course_assignments_assigned_by_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["batches.id", "batches.organization_id"],
            name="fk_course_assignments_batch_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "owner_organization_id"],
            ["courses.id", "courses.organization_id"],
            name="fk_course_assignments_course_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_course_assignments_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_course_assignments_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_assignment_id"],
            ["course_assignments.id"],
            name=op.f("fk_course_assignments_parent_assignment_id_course_assignments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_assignments")),
        sa.UniqueConstraint(
            "course_id",
            "organization_id",
            "batch_id",
            name="uq_course_assignments_course_org_batch",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_course_assignments_assigned_by_org_id",
        "course_assignments",
        ["assigned_by_org_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_assignments_batch_org",
        "course_assignments",
        ["batch_id", "organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_assignments_course_owner",
        "course_assignments",
        ["course_id", "owner_organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_assignments_created_by", "course_assignments", ["created_by"], unique=False
    )
    op.create_index(
        "ix_course_assignments_org_course",
        "course_assignments",
        ["organization_id", "course_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_assignments_parent", "course_assignments", ["parent_assignment_id"], unique=False
    )
    op.create_table(
        "course_modules",
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "organization_id"],
            ["courses.id", "courses.organization_id"],
            name="fk_course_modules_course_org",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_modules")),
        sa.UniqueConstraint(
            "course_id",
            "position",
            deferrable=True,
            initially="DEFERRED",
            name="uq_course_modules_course_position",
        ),
        sa.UniqueConstraint("id", "course_id", name="uq_course_modules_id_course_id"),
    )
    op.create_index(
        "ix_course_modules_course_org",
        "course_modules",
        ["course_id", "organization_id"],
        unique=False,
    )
    op.create_table(
        "course_versions",
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("major", sa.Integer(), nullable=False),
        sa.Column("minor", sa.Integer(), nullable=False),
        sa.Column("release_type", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("release_notes", sa.Text(), server_default="", nullable=False),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(release_type = 'major' AND minor = 0) OR (release_type = 'minor' AND minor > 0)",
            name=op.f("ck_course_versions_ck_course_versions_release_type"),
        ),
        sa.CheckConstraint(
            "major >= 1 AND minor >= 0", name=op.f("ck_course_versions_ck_course_versions_numbers")
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "organization_id"],
            ["courses.id", "courses.organization_id"],
            name="fk_course_versions_course_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["published_by"],
            ["users.id"],
            name=op.f("fk_course_versions_published_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_versions")),
        sa.UniqueConstraint(
            "course_id", "major", "minor", name="uq_course_versions_course_major_minor"
        ),
    )
    op.create_index(
        "ix_course_versions_course_org",
        "course_versions",
        ["course_id", "organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_versions_published_by", "course_versions", ["published_by"], unique=False
    )
    op.create_table(
        "catalog_entries",
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "skill_names",
            postgresql.ARRAY(sa.String(length=120)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("lesson_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_catalog_entries_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["course_versions.id"],
            name=op.f("fk_catalog_entries_version_id_course_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("course_id", name=op.f("pk_catalog_entries")),
        sa.UniqueConstraint("slug", name="uq_catalog_entries_slug"),
    )
    op.create_index(
        "ix_catalog_entries_organization_id", "catalog_entries", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_catalog_entries_version_id", "catalog_entries", ["version_id"], unique=False
    )
    op.create_table(
        "course_version_lessons",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("module_id", sa.Uuid(), nullable=False),
        sa.Column("module_position", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "lesson_type",
            LESSON_TYPE,
            nullable=False,
        ),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("completion_threshold", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("video_asset_id", sa.Uuid(), nullable=True),
        sa.Column("video_duration_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["course_versions.id"],
            name=op.f("fk_course_version_lessons_version_id_course_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("version_id", "lesson_id", name=op.f("pk_course_version_lessons")),
    )
    op.create_index(
        "ix_course_version_lessons_course_id", "course_version_lessons", ["course_id"], unique=False
    )
    op.create_index(
        "ix_course_version_lessons_lesson_id", "course_version_lessons", ["lesson_id"], unique=False
    )
    op.create_index(
        "ix_course_version_lessons_organization_id",
        "course_version_lessons",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_version_lessons_video_asset_id",
        "course_version_lessons",
        ["video_asset_id"],
        unique=False,
    )
    op.create_table(
        "enrollments",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("major_version", sa.Integer(), nullable=False),
        sa.Column("source_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("progress_percent", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("last_lesson_id", sa.Uuid(), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "enrolled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name=op.f("ck_enrollments_ck_enrollments_status")
        ),
        sa.CheckConstraint(
            "major_version >= 1", name=op.f("ck_enrollments_ck_enrollments_major_version")
        ),
        sa.CheckConstraint(
            "progress_percent BETWEEN 0 AND 100",
            name=op.f("ck_enrollments_ck_enrollments_progress_percent"),
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_enrollments_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_enrollments_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_assignment_id"],
            ["course_assignments.id"],
            name=op.f("fk_enrollments_source_assignment_id_course_assignments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_enrollments_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrollments")),
        sa.UniqueConstraint("user_id", "course_id", name="uq_enrollments_user_course"),
    )
    op.create_index("ix_enrollments_course_id", "enrollments", ["course_id"], unique=False)
    op.create_index(
        "ix_enrollments_org_course", "enrollments", ["organization_id", "course_id"], unique=False
    )
    op.create_index(
        "ix_enrollments_source_assignment_id", "enrollments", ["source_assignment_id"], unique=False
    )
    op.create_index(
        "ix_enrollments_user_last_accessed",
        "enrollments",
        ["user_id", "last_accessed_at"],
        unique=False,
    )
    op.create_table(
        "lessons",
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("module_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "lesson_type",
            LESSON_TYPE,
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("completion_threshold", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column(
            "content", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("estimated_minutes", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "completion_threshold IS NULL "
            "OR (completion_threshold > 0 AND completion_threshold <= 1)",
            name=op.f("ck_lessons_ck_lessons_completion_threshold"),
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "organization_id"],
            ["courses.id", "courses.organization_id"],
            name="fk_lessons_course_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_lessons_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["module_id", "course_id"],
            ["course_modules.id", "course_modules.course_id"],
            name="fk_lessons_module_course",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lessons")),
        sa.UniqueConstraint(
            "module_id",
            "position",
            deferrable=True,
            initially="DEFERRED",
            name="uq_lessons_module_position",
        ),
    )
    op.create_index(
        "ix_lessons_course_org", "lessons", ["course_id", "organization_id"], unique=False
    )
    op.create_index("ix_lessons_created_by", "lessons", ["created_by"], unique=False)
    op.create_index("ix_lessons_module_course", "lessons", ["module_id", "course_id"], unique=False)
    op.create_table(
        "lesson_progress",
        sa.Column("enrollment_id", sa.Uuid(), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="not_started", nullable=False),
        sa.Column("video_position_seconds", sa.Integer(), nullable=True),
        sa.Column("watched_segments", sa.LargeBinary(), nullable=True),
        sa.Column("watched_ratio", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("video_asset_id", sa.Uuid(), nullable=True),
        sa.Column("pdf_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('not_started', 'in_progress', 'completed')",
            name=op.f("ck_lesson_progress_ck_lesson_progress_status"),
        ),
        sa.CheckConstraint(
            "watched_ratio IS NULL OR (watched_ratio >= 0 AND watched_ratio <= 1)",
            name=op.f("ck_lesson_progress_ck_lesson_progress_watched_ratio"),
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id"],
            ["enrollments.id"],
            name=op.f("fk_lesson_progress_enrollment_id_enrollments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("enrollment_id", "lesson_id", name=op.f("pk_lesson_progress")),
    )
    op.create_index("ix_lesson_progress_lesson_id", "lesson_progress", ["lesson_id"], unique=False)
    op.create_index(
        "ix_lesson_progress_organization_id", "lesson_progress", ["organization_id"], unique=False
    )
    op.create_index("ix_lesson_progress_user_id", "lesson_progress", ["user_id"], unique=False)
    op.create_table(
        "lesson_skills",
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lesson_id"],
            ["lessons.id"],
            name=op.f("fk_lesson_skills_lesson_id_lessons"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skills.id"],
            name=op.f("fk_lesson_skills_skill_id_skills"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("lesson_id", "skill_id", name=op.f("pk_lesson_skills")),
    )
    op.create_index(
        "ix_lesson_skills_organization_id", "lesson_skills", ["organization_id"], unique=False
    )
    op.create_index("ix_lesson_skills_skill_id", "lesson_skills", ["skill_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_courses_current_version_id_course_versions"),
        "courses",
        "course_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _helpers()
    _privileges()
    _policies()


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_courses_current_version_id_course_versions"), "courses", type_="foreignkey"
    )
    op.drop_index("ix_lesson_skills_skill_id", table_name="lesson_skills")
    op.drop_index("ix_lesson_skills_organization_id", table_name="lesson_skills")
    op.drop_table("lesson_skills")
    op.drop_index("ix_lesson_progress_user_id", table_name="lesson_progress")
    op.drop_index("ix_lesson_progress_organization_id", table_name="lesson_progress")
    op.drop_index("ix_lesson_progress_lesson_id", table_name="lesson_progress")
    op.drop_table("lesson_progress")
    op.drop_index("ix_lessons_module_course", table_name="lessons")
    op.drop_index("ix_lessons_created_by", table_name="lessons")
    op.drop_index("ix_lessons_course_org", table_name="lessons")
    op.drop_table("lessons")
    op.drop_index("ix_enrollments_user_last_accessed", table_name="enrollments")
    op.drop_index("ix_enrollments_source_assignment_id", table_name="enrollments")
    op.drop_index("ix_enrollments_org_course", table_name="enrollments")
    op.drop_index("ix_enrollments_course_id", table_name="enrollments")
    op.drop_table("enrollments")
    op.drop_index("ix_course_version_lessons_video_asset_id", table_name="course_version_lessons")
    op.drop_index("ix_course_version_lessons_organization_id", table_name="course_version_lessons")
    op.drop_index("ix_course_version_lessons_lesson_id", table_name="course_version_lessons")
    op.drop_index("ix_course_version_lessons_course_id", table_name="course_version_lessons")
    op.drop_table("course_version_lessons")
    op.drop_index("ix_catalog_entries_version_id", table_name="catalog_entries")
    op.drop_index("ix_catalog_entries_organization_id", table_name="catalog_entries")
    op.drop_table("catalog_entries")
    op.drop_index("ix_course_versions_published_by", table_name="course_versions")
    op.drop_index("ix_course_versions_course_org", table_name="course_versions")
    op.drop_table("course_versions")
    op.drop_index("ix_course_modules_course_org", table_name="course_modules")
    op.drop_table("course_modules")
    op.drop_index("ix_course_assignments_parent", table_name="course_assignments")
    op.drop_index("ix_course_assignments_org_course", table_name="course_assignments")
    op.drop_index("ix_course_assignments_created_by", table_name="course_assignments")
    op.drop_index("ix_course_assignments_course_owner", table_name="course_assignments")
    op.drop_index("ix_course_assignments_batch_org", table_name="course_assignments")
    op.drop_index("ix_course_assignments_assigned_by_org_id", table_name="course_assignments")
    op.drop_table("course_assignments")
    op.drop_index("ix_video_assets_organization_id_status", table_name="video_assets")
    op.drop_index("ix_video_assets_created_by", table_name="video_assets")
    op.drop_table("video_assets")
    op.drop_index("ix_files_organization_id_status", table_name="files")
    op.drop_index("ix_files_created_by", table_name="files")
    op.drop_table("files")
    op.drop_index("ix_courses_organization_id_status", table_name="courses")
    op.drop_index("ix_courses_current_version_id", table_name="courses")
    op.drop_index("ix_courses_created_by", table_name="courses")
    op.drop_table("courses")
    op.drop_index("ix_skills_path_gist", table_name="skills", postgresql_using="gist")
    op.drop_index("ix_skills_parent_id", table_name="skills")
    op.drop_table("skills")
    op.execute("DROP TYPE IF EXISTS lesson_type")
    for fn in _HELPER_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")


# ============================================================================ RLS helpers

_HELPER_SIGNATURES = (
    "app.course_readable(uuid)",
    "app.is_org_grant(uuid, uuid, uuid)",
)

EDITOR_ROLES = "'{instructor,org_admin}'::text[]"
STAFF_ROLES_ALL = "'{org_admin,instructor,lab_author}'::text[]"


def _helpers() -> None:
    definer = "STABLE SECURITY DEFINER SET search_path = pg_catalog, public"
    # A user may read a (published) course through an assignment to their current org: the org's
    # admins/instructors via any assignment row, students only via their batch's assignment.
    op.execute(
        f"""
        CREATE FUNCTION app.course_readable(course uuid) RETURNS boolean
        LANGUAGE sql {definer}
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM public.course_assignments a
            WHERE a.course_id = course
              AND a.organization_id = app.current_org_id()
              AND (
                app.current_user_has_role(a.organization_id, {EDITOR_ROLES})
                OR (a.batch_id IS NOT NULL AND app.current_user_in_batch(a.batch_id))
              )
          )
        $$
        """
    )
    # Used by the narrowing policy (reading course_assignments from its own policy would recurse).
    op.execute(
        f"""
        CREATE FUNCTION app.is_org_grant(grant_id uuid, course uuid, org uuid) RETURNS boolean
        LANGUAGE sql {definer}
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM public.course_assignments a
            WHERE a.id = grant_id AND a.course_id = course AND a.organization_id = org
              AND a.batch_id IS NULL
          )
        $$
        """
    )
    for sig in _HELPER_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {sig} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {sig} TO {APP_ROLE}")


def _privileges() -> None:
    # Published versions and their lesson rows are immutable; assignments are create/delete only.
    for table in ("course_versions", "course_version_lessons"):
        op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE ON course_assignments FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE ON lesson_skills FROM {APP_ROLE}")


def _editor(org_column: str = "organization_id") -> str:
    """Owner-org course editors (instructor/org_admin) acting in that org, or platform admins."""
    return (
        f"({PLATFORM_ADMIN} OR ({org_column} = app.current_org_id() AND "
        f"(SELECT app.current_user_has_role(app.current_org_id(), {EDITOR_ROLES}))))"
    )


def _policies() -> None:
    editor = _editor()

    # --- skills: global taxonomy
    skill_writer = (
        f"({PLATFORM_ADMIN} OR ((SELECT app.org_is_content_publisher(app.current_org_id())) AND "
        f"(SELECT app.current_user_has_role(app.current_org_id(), {STAFF_ROLES_ALL}))))"
    )
    enable_rls("skills")
    policy("skills", "select", using="true")
    policy("skills", "insert", check=skill_writer)
    policy("skills", "update", using=skill_writer, check=skill_writer)
    policy("skills", "delete", using=skill_writer)

    # --- courses
    enable_rls("courses")
    policy("courses", "select", using=f"{editor} OR app.course_readable(id)")
    policy("courses", "insert", check=editor)
    policy("courses", "update", using=editor, check=editor)
    policy("courses", "delete", using=editor)

    # --- draft tree: editors only (assigned orgs and students read published versions)
    for table in ("course_modules", "lessons"):
        enable_rls(table)
        policy(table, "select", using=editor)
        policy(table, "insert", check=editor)
        policy(table, "update", using=editor, check=editor)
        policy(table, "delete", using=editor)
    enable_rls("lesson_skills")
    policy("lesson_skills", "select", using=editor)
    policy("lesson_skills", "insert", check=editor)
    policy("lesson_skills", "delete", using=editor)

    # --- published versions: editors write (publish); readers see them
    for table in ("course_versions", "course_version_lessons"):
        enable_rls(table)
        policy(table, "select", using=f"{editor} OR app.course_readable(course_id)")
        policy(table, "insert", check=editor)

    # --- assignments
    owner_editor = _editor("owner_organization_id")
    receiving_admin = (
        "(organization_id = app.current_org_id() AND "
        "(SELECT app.current_user_has_role(app.current_org_id(), '{org_admin}'::text[])))"
    )
    receiving_staff = (
        "(organization_id = app.current_org_id() AND "
        f"(SELECT app.current_user_has_role(app.current_org_id(), {EDITOR_ROLES})))"
    )
    publisher_made = (
        f"({owner_editor} AND assigned_by_org_id = owner_organization_id "
        "AND parent_assignment_id IS NULL "
        "AND (organization_id = owner_organization_id "
        "OR app.org_is_content_publisher(owner_organization_id)))"
    )
    narrowing = (
        f"({receiving_admin} AND assigned_by_org_id = organization_id "
        "AND batch_id IS NOT NULL AND parent_assignment_id IS NOT NULL "
        "AND app.is_org_grant(parent_assignment_id, course_id, organization_id))"
    )
    enable_rls("course_assignments")
    policy("course_assignments", "select", using=f"{owner_editor} OR {receiving_staff}")
    policy("course_assignments", "insert", check=f"{publisher_made} OR {narrowing}")
    policy(
        "course_assignments",
        "delete",
        using=(
            f"({owner_editor} AND assigned_by_org_id = owner_organization_id) OR "
            f"({receiving_admin} AND assigned_by_org_id = organization_id)"
        ),
    )

    # --- public catalog: anyone reads public cards; the owner's editors write them
    enable_rls("catalog_entries")
    policy("catalog_entries", "select", using="true")
    policy("catalog_entries", "insert", check=editor)
    policy("catalog_entries", "update", using=editor, check=editor)
    policy("catalog_entries", "delete", using=editor)

    # --- enrollments & progress (organization_id = the student's org)
    own = "(organization_id = app.current_org_id() AND user_id = app.current_user_id())"
    org_staff = (
        "(organization_id = app.current_org_id() AND "
        f"(SELECT app.current_user_has_role(app.current_org_id(), {EDITOR_ROLES})))"
    )
    org_admin = (
        "(organization_id = app.current_org_id() AND "
        "(SELECT app.current_user_has_role(app.current_org_id(), '{org_admin}'::text[])))"
    )
    for table in ("enrollments", "lesson_progress"):
        enable_rls(table)
        policy(table, "select", using=f"{PLATFORM_ADMIN} OR {own} OR {org_staff}")
        # Students record their own progress; org admins (and system jobs acting as platform
        # admins) create and upgrade enrollments.
        writer = f"{PLATFORM_ADMIN} OR {own} OR {org_admin}"
        policy(table, "insert", check=writer)
        policy(table, "update", using=writer, check=writer)
        policy(table, "delete", using=PLATFORM_ADMIN)

    # --- media: the owner org's course editors
    for table in ("video_assets", "files"):
        enable_rls(table)
        policy(table, "select", using=editor)
        policy(table, "insert", check=editor)
        policy(table, "update", using=editor, check=editor)
        policy(table, "delete", using=editor)
