"""Assignments, thin slice (Phase 2.5): definitions, one active submission per student, grades.

- `assignments` belongs to the course **owner** org and is part of the draft (editors only, like
  lessons). Students and graders read the published copy in the version snapshot.
- `assignment_submissions` and `assignment_grades` belong to the **student's** org. Students
  write and read their own submission; the org's `instructor`s and `org_admin`s grade it. The
  course owner org sees nothing of another org's submissions. `assignment_grades` is unique per
  submission for now; Phase 3 drops that constraint (grade history) and adds
  `submission_attempts` next to these tables without renaming them.
- Students upload submission files (`files.kind = 'submission'`) and read only their own.
- Completion on grade: a grader may write `lesson_progress` (and an enrollment's progress) only
  where a graded submission exists for that enrollment and lesson (`app.lesson_graded`,
  `app.enrollment_graded`). A trigger stops graders changing anything on an enrollment except
  its progress columns.

Revision ID: 0011
Revises: 0010
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.rls import APP_ROLE, PLATFORM_ADMIN, enable_rls, policy

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

HERE = "organization_id = app.current_org_id()"
EDITOR = (
    f"({PLATFORM_ADMIN} OR ({HERE} AND "
    "(SELECT app.current_user_has_role(app.current_org_id(), '{instructor,org_admin}'::text[]))))"
)
GRADERS = (
    f"({HERE} AND "
    "(SELECT app.current_user_has_role(app.current_org_id(), '{instructor,org_admin}'::text[])))"
)
INSTRUCTOR = (
    f"({HERE} AND "
    "(SELECT app.current_user_has_role(app.current_org_id(), '{instructor}'::text[])))"
)
OWN = f"({HERE} AND user_id = app.current_user_id())"
OWN_SUBMISSION_FILE = f"(kind = 'submission' AND created_by = app.current_user_id() AND {HERE})"
DEFINER = "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public"

# Policies replaced below, as they were before this migration (for downgrade).
OLD_PROGRESS_WRITER = (
    f"{PLATFORM_ADMIN} OR {OWN} OR ({HERE} AND "
    "(SELECT app.current_user_has_role(app.current_org_id(), '{org_admin}'::text[])))"
)


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    # ------------------------------------------------------------------ assignments (draft)
    op.create_table(
        "assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column(
            "lesson_id", sa.Uuid(), sa.ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("instructions", postgresql.JSONB(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_marks", sa.Integer(), nullable=False),
        sa.Column("submission_kinds", postgresql.ARRAY(sa.String(10)), nullable=False),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["course_id", "organization_id"],
            ["courses.id", "courses.organization_id"],
            ondelete="CASCADE",
            name="fk_assignments_course_id_courses",
        ),
        sa.UniqueConstraint("lesson_id", name="uq_assignments_lesson_id"),
        sa.CheckConstraint("max_marks BETWEEN 1 AND 1000", name="ck_assignments_max_marks"),
        sa.CheckConstraint(
            "cardinality(submission_kinds) >= 1 "
            "AND submission_kinds <@ ARRAY['file', 'text']::varchar[]",
            name="ck_assignments_submission_kinds",
        ),
    )
    op.create_index("ix_assignments_course_org", "assignments", ["course_id", "organization_id"])
    op.create_index("ix_assignments_organization_id", "assignments", ["organization_id"])
    op.create_index("ix_assignments_created_by", "assignments", ["created_by"])
    enable_rls("assignments")
    policy("assignments", "select", using=EDITOR)
    policy("assignments", "insert", check=EDITOR)
    policy("assignments", "update", using=EDITOR, check=EDITOR)
    policy("assignments", "delete", using=EDITOR)

    # ------------------------------------------------------------------ submissions
    # assignment_id and lesson_id are not foreign keys: the draft rows they name may be deleted
    # later, while the published version (and the student's work) remain.
    op.create_table(
        "assignment_submissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column(
            "course_id", sa.Uuid(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("course_versions.id"), nullable=False),
        sa.Column(
            "enrollment_id",
            sa.Uuid(),
            sa.ForeignKey("enrollments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=True),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("files.id"), nullable=True),
        sa.Column("status", sa.String(20), server_default="submitted", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "enrollment_id", "assignment_id", name="uq_assignment_submissions_enrollment_assignment"
        ),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_assignment_submissions_id_organization_id"
        ),
        sa.CheckConstraint(
            "status IN ('submitted', 'graded')", name="ck_assignment_submissions_status"
        ),
        sa.CheckConstraint(
            "(kind = 'text' AND text_body IS NOT NULL AND file_id IS NULL) "
            "OR (kind = 'file' AND file_id IS NOT NULL AND text_body IS NULL)",
            name="ck_assignment_submissions_content",
        ),
        sa.CheckConstraint(
            "text_body IS NULL OR char_length(text_body) <= 20000",
            name="ck_assignment_submissions_text_length",
        ),
    )
    # The graders' list: an org's submissions for one lesson, ungraded first, oldest first.
    op.create_index(
        "ix_assignment_submissions_queue",
        "assignment_submissions",
        ["organization_id", "lesson_id", "status", "submitted_at", "id"],
    )
    op.create_index(
        "ix_assignment_submissions_enrollment_lesson",
        "assignment_submissions",
        ["enrollment_id", "lesson_id"],
    )
    for column in ("course_id", "version_id", "user_id", "file_id"):
        op.create_index(f"ix_assignment_submissions_{column}", "assignment_submissions", [column])
    enabled_enrollment = (
        "EXISTS (SELECT 1 FROM public.enrollments e WHERE e.id = enrollment_id "
        "AND e.user_id = app.current_user_id() AND e.organization_id = app.current_org_id() "
        "AND e.status = 'active')"
    )
    student_open = f"({OWN} AND status = 'submitted')"
    enable_rls("assignment_submissions")
    policy("assignment_submissions", "select", using=f"{PLATFORM_ADMIN} OR {OWN} OR {GRADERS}")
    policy(
        "assignment_submissions",
        "insert",
        check=f"{PLATFORM_ADMIN} OR ({student_open} AND {enabled_enrollment})",
    )
    policy(
        "assignment_submissions",
        "update",
        using=f"{PLATFORM_ADMIN} OR {student_open} OR {GRADERS}",
        check=f"{PLATFORM_ADMIN} OR ({student_open} AND {enabled_enrollment}) OR {GRADERS}",
    )
    policy("assignment_submissions", "delete", using=PLATFORM_ADMIN)

    # ------------------------------------------------------------------ grades
    op.create_table(
        "assignment_grades",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("score", sa.Numeric(7, 2), nullable=False),
        sa.Column("max_marks", sa.Integer(), nullable=False),
        sa.Column("feedback", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "graded_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "graded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["submission_id", "organization_id"],
            ["assignment_submissions.id", "assignment_submissions.organization_id"],
            ondelete="CASCADE",
            name="fk_assignment_grades_submission",
        ),
        # One grade per submission for now (Phase 3: drop this to keep grade history).
        sa.UniqueConstraint(
            "submission_id", "organization_id", name="uq_assignment_grades_submission"
        ),
        sa.CheckConstraint("score >= 0 AND score <= max_marks", name="ck_assignment_grades_score"),
        sa.CheckConstraint(
            "char_length(feedback) <= 10000", name="ck_assignment_grades_feedback_length"
        ),
    )
    op.create_index(
        "ix_assignment_grades_organization_id", "assignment_grades", ["organization_id"]
    )
    op.create_index("ix_assignment_grades_user_id", "assignment_grades", ["user_id"])
    op.create_index("ix_assignment_grades_graded_by", "assignment_grades", ["graded_by"])
    enable_rls("assignment_grades")
    policy("assignment_grades", "select", using=f"{PLATFORM_ADMIN} OR {OWN} OR {GRADERS}")
    policy("assignment_grades", "insert", check=f"{PLATFORM_ADMIN} OR {GRADERS}")
    policy(
        "assignment_grades",
        "update",
        using=f"{PLATFORM_ADMIN} OR {GRADERS}",
        check=f"{PLATFORM_ADMIN} OR {GRADERS}",
    )
    policy("assignment_grades", "delete", using=PLATFORM_ADMIN)

    # ------------------------------------------------------------------ submission files
    op.drop_constraint("ck_files_kind", "files", type_="check")
    op.create_check_constraint("ck_files_kind", "files", "kind IN ('pdf', 'image', 'submission')")
    for command in ("select", "insert", "update"):
        op.execute(f"DROP POLICY files_{command} ON files")
    policy("files", "select", using=f"{EDITOR} OR app.file_readable(id) OR {OWN_SUBMISSION_FILE}")
    student_upload = (
        f"({OWN_SUBMISSION_FILE} AND (SELECT app.current_user_is_member(app.current_org_id())))"
    )
    policy("files", "insert", check=f"{EDITOR} OR {student_upload}")
    policy(
        "files",
        "update",
        using=f"{EDITOR} OR {OWN_SUBMISSION_FILE}",
        check=f"{EDITOR} OR {student_upload}",
    )

    # ------------------------------------------------------------------ completion on grade
    op.execute(f"""
        CREATE FUNCTION app.lesson_graded(enrollment uuid, lesson uuid) RETURNS boolean {DEFINER}
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM public.assignment_submissions s
            JOIN public.assignment_grades g ON g.submission_id = s.id
            WHERE s.enrollment_id = enrollment AND s.lesson_id = lesson
              AND s.organization_id = app.current_org_id()
          )
        $$
    """)
    op.execute(f"""
        CREATE FUNCTION app.enrollment_graded(enrollment uuid) RETURNS boolean {DEFINER}
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM public.assignment_submissions s
            JOIN public.assignment_grades g ON g.submission_id = s.id
            WHERE s.enrollment_id = enrollment AND s.organization_id = app.current_org_id()
          )
        $$
    """)
    for signature in ("app.lesson_graded(uuid, uuid)", "app.enrollment_graded(uuid)"):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {APP_ROLE}")

    progress_writer = (
        f"{OLD_PROGRESS_WRITER} OR ({INSTRUCTOR} AND app.lesson_graded(enrollment_id, lesson_id))"
    )
    enrollment_writer = f"{OLD_PROGRESS_WRITER} OR ({INSTRUCTOR} AND app.enrollment_graded(id))"
    for table, writer in (("lesson_progress", progress_writer), ("enrollments", enrollment_writer)):
        op.execute(f"DROP POLICY {table}_insert ON {table}")
        op.execute(f"DROP POLICY {table}_update ON {table}")
        policy(table, "insert", check=writer)
        policy(table, "update", using=writer, check=writer)

    # RLS can't limit columns: a grader's UPDATE of an enrollment may change only its progress.
    op.execute(f"""
        CREATE FUNCTION app.enrollments_grader_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public
        AS $$
        BEGIN
          IF current_user = '{APP_ROLE}'
             AND NOT app.current_user_is_platform_admin()
             AND NEW.user_id IS DISTINCT FROM app.current_user_id()
             AND NOT app.current_user_has_role(NEW.organization_id, '{{org_admin}}'::text[])
             AND (to_jsonb(NEW) - ARRAY['progress_percent', 'completed_at', 'updated_at'])
                 IS DISTINCT FROM
                 (to_jsonb(OLD) - ARRAY['progress_percent', 'completed_at', 'updated_at'])
          THEN
            RAISE EXCEPTION 'graders may only update enrollment progress' USING ERRCODE = '42501';
          END IF;
          RETURN NEW;
        END
        $$
    """)
    op.execute(
        "CREATE TRIGGER enrollments_grader_guard BEFORE UPDATE ON enrollments "
        "FOR EACH ROW EXECUTE FUNCTION app.enrollments_grader_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER enrollments_grader_guard ON enrollments")
    op.execute("DROP FUNCTION app.enrollments_grader_guard()")
    for table in ("lesson_progress", "enrollments"):
        op.execute(f"DROP POLICY {table}_insert ON {table}")
        op.execute(f"DROP POLICY {table}_update ON {table}")
        policy(table, "insert", check=OLD_PROGRESS_WRITER)
        policy(table, "update", using=OLD_PROGRESS_WRITER, check=OLD_PROGRESS_WRITER)
    op.execute("DROP FUNCTION app.enrollment_graded(uuid)")
    op.execute("DROP FUNCTION app.lesson_graded(uuid, uuid)")

    op.drop_table("assignment_grades")
    op.drop_table("assignment_submissions")
    op.drop_table("assignments")

    for command in ("select", "insert", "update"):
        op.execute(f"DROP POLICY files_{command} ON files")
    policy("files", "select", using=f"{EDITOR} OR app.file_readable(id)")
    policy("files", "insert", check=EDITOR)
    policy("files", "update", using=EDITOR, check=EDITOR)
    op.execute("DELETE FROM files WHERE kind = 'submission'")
    op.drop_constraint("ck_files_kind", "files", type_="check")
    op.create_check_constraint("ck_files_kind", "files", "kind IN ('pdf', 'image')")
