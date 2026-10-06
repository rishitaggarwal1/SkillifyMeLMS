"""Immutable assignment attempts, grade revisions and frozen rubric/late rules.

Revision ID: 0015
Revises: 0014
"""

from pathlib import Path

from alembic import op

from app.db.rls import APP_ROLE, PLATFORM_ADMIN, enable_rls, policy

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

OWN = "organization_id=app.current_org_id() AND user_id=app.current_user_id()"
GRADER = (
    "organization_id=app.current_org_id() AND "
    "app.current_user_has_role(app.current_org_id(), "
    "'{instructor,org_admin}'::text[])"
)


def upgrade() -> None:
    sql = (Path(__file__).resolve().parents[1] / "sql" / "0015_assignment_history.sql").read_text(
        encoding="utf-8"
    )
    for statement in sql.split("-- statement"):
        op.execute(statement)
    guards = (Path(__file__).resolve().parents[1] / "sql" / "0015_assignment_guards.sql").read_text(
        encoding="utf-8"
    )
    for statement in guards.split("-- statement"):
        op.execute(statement)
    op.execute("""CREATE FUNCTION app.lock_assessment_enrollment(enrollment uuid) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
      DECLARE found_id uuid;
      BEGIN
        SELECT e.id INTO found_id FROM public.enrollments e WHERE e.id=enrollment
          AND (app.current_user_is_platform_admin() OR (e.organization_id=app.current_org_id()
            AND app.current_user_is_member(e.organization_id)
            AND (e.user_id=app.current_user_id() OR
          app.current_user_has_role(e.organization_id,'{instructor,org_admin}'::text[]))))
          FOR UPDATE;
        RETURN found_id IS NOT NULL;
      END $$""")
    op.execute("REVOKE ALL ON FUNCTION app.lock_assessment_enrollment(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.lock_assessment_enrollment(uuid) TO {APP_ROLE}")
    enable_rls("submission_attempts")
    policy("submission_attempts", "select", using=f"{PLATFORM_ADMIN} OR ({OWN}) OR ({GRADER})")
    policy(
        "submission_attempts",
        "insert",
        check=f"({OWN}) AND EXISTS (SELECT 1 FROM assignment_submissions s WHERE "
        "s.id=submission_id AND s.user_id=app.current_user_id() AND "
        "s.status='submitted')",
    )
    policy("submission_attempts", "update", using="false", check="false")
    policy("submission_attempts", "delete", using="false")
    op.execute(f"REVOKE UPDATE, DELETE ON submission_attempts, assignment_grades FROM {APP_ROLE}")
    op.execute("DROP POLICY assignment_grades_insert ON assignment_grades")
    policy(
        "assignment_grades",
        "insert",
        check=f"({PLATFORM_ADMIN} OR ({GRADER})) AND "
        "user_id<>app.current_user_id() AND EXISTS (SELECT 1 "
        "FROM "
        "assignment_submissions s WHERE s.id=submission_id AND "
        "s.active_attempt_id=attempt_id AND s.user_id=assignment_grades.user_id)",
    )
    for name, condition in (("lesson_graded", "s.lesson_id=lesson AND"), ("enrollment_graded", "")):
        args = "enrollment uuid, lesson uuid" if condition else "enrollment uuid"
        op.execute(f"""CREATE OR REPLACE FUNCTION app.{name}({args}) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
          SELECT EXISTS (SELECT 1 FROM public.assignment_submissions s
            JOIN public.assignment_grades g ON g.attempt_id=s.active_attempt_id AND
          g.submission_id=s.id
            JOIN public.submission_attempts a ON a.id=s.active_attempt_id
            JOIN public.enrollments e ON e.id=s.enrollment_id AND e.major_version=a.major_version
            WHERE {condition} s.enrollment_id=enrollment AND s.organization_id=app.current_org_id())
        $$""")
    op.execute("""CREATE FUNCTION app.assignment_image_readable(file uuid) RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
      SELECT EXISTS (SELECT 1 FROM public.submission_attempts a
        JOIN public.assignment_submissions s ON s.id=a.submission_id
        JOIN public.enrollments e ON e.id=s.enrollment_id
        WHERE a.assignment_rules->'image_file_ids' @> to_jsonb(ARRAY[file::text])
          AND s.organization_id=app.current_org_id() AND e.status='active'
          AND ((s.user_id=app.current_user_id() AND
          app.student_course_assigned(s.user_id,s.organization_id,s.course_id))
            OR (app.current_user_has_role(s.organization_id,'{instructor,org_admin}'::text[])
                AND app.course_readable(s.course_id))))
      $$""")
    op.execute("REVOKE ALL ON FUNCTION app.assignment_image_readable(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.assignment_image_readable(uuid) TO {APP_ROLE}")
    op.execute("DROP POLICY files_select ON files")
    policy(
        "files",
        "select",
        using="app.current_user_is_platform_admin() OR "
        "(organization_id=app.current_org_id() AND "
        "app.current_user_has_role(app.current_org_id(),'{instructor,org_admin}'::text[])) "
        "OR "
        "app.file_readable(id) OR app.assignment_image_readable(id) OR "
        "(kind='submission' AND created_by=app.current_user_id() AND "
        "organization_id=app.current_org_id())",
    )
    op.execute("""CREATE FUNCTION app.submission_projection_guard() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
      DECLARE s public.assignment_submissions; a public.submission_attempts;
      BEGIN
        SELECT * INTO s FROM public.assignment_submissions WHERE id=NEW.id;
        IF NOT FOUND THEN RETURN NULL; END IF;
        SELECT * INTO a FROM public.submission_attempts WHERE id=s.active_attempt_id;
        IF NOT FOUND OR a.submission_id<>s.id OR a.organization_id<>s.organization_id
          OR a.user_id<>s.user_id OR a.kind<>s.kind OR a.version_id<>s.version_id
          OR a.text_body IS DISTINCT FROM s.text_body OR a.file_id IS DISTINCT FROM s.file_id
          OR a.submitted_at<>s.submitted_at THEN
          RAISE EXCEPTION 'submission must project its active immutable attempt' USING
          ERRCODE='23514';
        END IF;
        IF s.status='graded' AND NOT EXISTS(SELECT 1 FROM public.assignment_grades g WHERE
          g.attempt_id=a.id AND g.submission_id=s.id) THEN
          RAISE EXCEPTION 'graded submission needs its active grade' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
      END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER submission_projection_guard AFTER INSERT OR "
        "UPDATE ON assignment_submissions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION app.submission_projection_guard()"
    )


def downgrade() -> None:
    # Collapse only to the active work/latest grade that the old schema can represent.
    op.execute("DROP TRIGGER assignment_attempt_guard ON submission_attempts")
    op.execute("DROP TRIGGER assignment_grade_guard ON assignment_grades")
    op.execute("DROP FUNCTION app.assignment_attempt_guard()")
    op.execute("DROP FUNCTION app.assignment_grade_guard()")
    op.execute("DROP TRIGGER submission_projection_guard ON assignment_submissions")
    op.execute("DROP FUNCTION app.submission_projection_guard()")
    op.execute("DROP FUNCTION app.lock_assessment_enrollment(uuid)")
    op.execute("DROP POLICY files_select ON files")
    policy(
        "files",
        "select",
        using="app.current_user_is_platform_admin() OR "
        "(organization_id=app.current_org_id() AND "
        "app.current_user_has_role(app.current_org_id(),'{instructor,org_admin}'::text[])) "
        "OR "
        "app.file_readable(id) OR (kind='submission' AND "
        "created_by=app.current_user_id() AND organization_id=app.current_org_id())",
    )
    op.execute("DROP FUNCTION app.assignment_image_readable(uuid)")
    for name, condition in (("lesson_graded", "s.lesson_id=lesson AND"), ("enrollment_graded", "")):
        args = "enrollment uuid, lesson uuid" if condition else "enrollment uuid"
        op.execute(f"""CREATE OR REPLACE FUNCTION app.{name}({args}) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
        SELECT EXISTS(SELECT 1 FROM public.assignment_submissions s JOIN
          public.assignment_grades g ON g.submission_id=s.id
        WHERE {condition} s.enrollment_id=enrollment AND s.organization_id=app.current_org_id())
        $$""")
    op.execute(
        "DELETE FROM assignment_grades g WHERE NOT EXISTS(SELECT 1 FROM "
        "assignment_submissions s WHERE s.id=g.submission_id AND "
        "s.active_attempt_id=g.attempt_id) OR EXISTS(SELECT 1 FROM assignment_grades "
        "newer WHERE newer.attempt_id=g.attempt_id AND "
        "newer.grade_sequence>g.grade_sequence)"
    )
    op.execute("ALTER TABLE assignment_grades DROP CONSTRAINT fk_assignment_grades_attempt")
    op.execute(
        "ALTER TABLE assignment_grades DROP CONSTRAINT uq_assignment_grades_attempt_sequence"
    )
    op.execute(
        "ALTER TABLE assignment_grades DROP CONSTRAINT "
        "ck_assignment_grades_ck_assignment_grades_sequence"
    )
    op.execute(
        "ALTER TABLE assignment_grades DROP CONSTRAINT "
        "ck_assignment_grades_ck_assignment_grades_penalty"
    )
    op.execute("DROP POLICY assignment_grades_insert ON assignment_grades")
    op.drop_index("ix_assignment_grades_attempt_org", table_name="assignment_grades")
    for name in (
        "attempt_id",
        "grade_sequence",
        "rubric_breakdown",
        "raw_score",
        "penalty_percent",
        "penalty_marks",
    ):
        op.drop_column("assignment_grades", name)
    op.create_unique_constraint(
        "uq_assignment_grades_submission", "assignment_grades", ["submission_id", "organization_id"]
    )
    op.drop_index("ix_assignment_grades_submission_id", table_name="assignment_grades")
    op.drop_index("ix_assignment_grades_submission_org", table_name="assignment_grades")
    policy("assignment_grades", "insert", check=f"{PLATFORM_ADMIN} OR ({GRADER})")
    op.execute(f"GRANT UPDATE,DELETE ON assignment_grades TO {APP_ROLE}")
    op.execute(
        "ALTER TABLE assignment_submissions DROP CONSTRAINT "
        "fk_assignment_submissions_active_attempt"
    )
    op.drop_column("assignment_submissions", "active_attempt_id")
    op.drop_table("submission_attempts")
    op.drop_column("assignments", "rubric")
    op.drop_column("assignments", "late_policy")
