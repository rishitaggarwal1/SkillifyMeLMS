"""Files used by published lessons (PDFs, notes images) and who may read them.

`course_version_lessons.file_ids` lists the files a published lesson uses: the PDF of a pdf
lesson, the images of a notes lesson. `app.file_readable` lets an actively enrolled student read
exactly those files of the version they are shown, mirroring `app.video_readable`.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.rls import APP_ROLE, PLATFORM_ADMIN, policy

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

EDITOR = (
    f"{PLATFORM_ADMIN} OR (organization_id = app.current_org_id() AND "
    "app.current_user_has_role(app.current_org_id(), '{instructor,org_admin}'::text[]))"
)


def upgrade() -> None:
    op.add_column(
        "course_version_lessons",
        sa.Column(
            "file_ids",
            postgresql.ARRAY(sa.Uuid()),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
    )
    op.create_index(
        "ix_course_version_lessons_file_ids",
        "course_version_lessons",
        ["file_ids"],
        postgresql_using="gin",
    )
    # Versions published before this migration: their pdf lessons' file, from the snapshot.
    op.execute("""
        UPDATE course_version_lessons vl
        SET file_ids = ARRAY[(l.lesson -> 'content' ->> 'file_id')::uuid]
        FROM course_versions v,
             LATERAL jsonb_path_query(v.snapshot, '$.modules[*].lessons[*]') AS l(lesson)
        WHERE vl.version_id = v.id
          AND vl.lesson_type = 'pdf'
          AND l.lesson ->> 'id' = vl.lesson_id::text
          AND l.lesson -> 'content' ->> 'file_id' IS NOT NULL
    """)

    op.create_check_constraint("ck_files_kind", "files", "kind IN ('pdf', 'image')")
    op.create_index("ix_files_organization_id_kind", "files", ["organization_id", "kind"])

    op.execute("""
        CREATE FUNCTION app.file_readable(file uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM public.course_version_lessons vl
            JOIN public.course_versions v ON v.id = vl.version_id
            JOIN public.enrollments e ON e.course_id = v.course_id
              AND e.major_version = v.major
            WHERE vl.file_ids @> ARRAY[file]
              AND e.organization_id = app.current_org_id()
              AND e.user_id = app.current_user_id() AND e.status = 'active'
              AND app.course_readable(e.course_id)
              AND NOT EXISTS (
                SELECT 1 FROM public.course_versions newer
                WHERE newer.course_id = v.course_id AND newer.major = v.major
                  AND newer.minor > v.minor
              )
          )
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION app.file_readable(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.file_readable(uuid) TO {APP_ROLE}")
    op.execute("DROP POLICY files_select ON files")
    policy("files", "select", using=f"{EDITOR} OR app.file_readable(id)")


def downgrade() -> None:
    op.execute("DROP POLICY files_select ON files")
    policy("files", "select", using=EDITOR)
    op.execute("DROP FUNCTION app.file_readable(uuid)")
    op.drop_index("ix_files_organization_id_kind", table_name="files")
    op.drop_constraint("ck_files_kind", "files", type_="check")
    op.drop_index("ix_course_version_lessons_file_ids", table_name="course_version_lessons")
    op.drop_column("course_version_lessons", "file_ids")
