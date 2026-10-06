"""Read-only role dashboards and their query indexes.

Revision ID: 0016
Revises: 0015
"""

from alembic import op

from app.db.rls import APP_ROLE

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_assignment_submissions_org_queue",
        "assignment_submissions",
        ["organization_id", "status", "submitted_at", "id"],
    )
    op.create_index(
        "ix_assignment_grades_org_user_recent",
        "assignment_grades",
        ["organization_id", "user_id", "graded_at", "id"],
    )
    op.create_index(
        "ix_quiz_attempts_org_state_recent",
        "quiz_attempts",
        ["organization_id", "state", "submitted_at", "id"],
    )
    op.create_index("ix_import_jobs_org_status", "import_jobs", ["organization_id", "status"])
    op.execute("""CREATE FUNCTION app.quiz_dashboard_outcomes()
      RETURNS TABLE(passed bigint, failed bigint)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        SELECT count(*) FILTER (WHERE a.passed), count(*) FILTER (WHERE NOT a.passed)
        FROM public.quiz_attempts a JOIN public.enrollments e ON e.id=a.enrollment_id
        WHERE a.organization_id=app.current_org_id() AND e.organization_id=app.current_org_id()
          AND (app.current_user_is_platform_admin() OR
            (app.current_user_is_member(app.current_org_id()) AND
            app.current_user_has_role(app.current_org_id(),'{instructor,org_admin}'::text[])))
          AND a.state='submitted' AND a.submitted_at >= now()-interval '7 days'
          AND e.status='active' AND app.course_readable(a.course_id)
          AND app.student_course_assigned(a.user_id,a.organization_id,a.course_id)
      $$""")
    op.execute("REVOKE ALL ON FUNCTION app.quiz_dashboard_outcomes() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.quiz_dashboard_outcomes() TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION app.quiz_dashboard_outcomes()")
    for table, name in (
        ("import_jobs", "ix_import_jobs_org_status"),
        ("quiz_attempts", "ix_quiz_attempts_org_state_recent"),
        ("assignment_grades", "ix_assignment_grades_org_user_recent"),
        ("assignment_submissions", "ix_assignment_submissions_org_queue"),
    ):
        op.drop_index(name, table_name=table)
