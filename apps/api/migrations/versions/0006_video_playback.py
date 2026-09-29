"""Video playback visibility and retry-safe progress flushes.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

from app.db.rls import APP_ROLE, PLATFORM_ADMIN, policy

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

EDITOR = (
    f"{PLATFORM_ADMIN} OR (organization_id = app.current_org_id() AND "
    "app.current_user_has_role(app.current_org_id(), '{instructor,org_admin}'::text[]))"
)


def upgrade() -> None:
    op.add_column("lesson_progress", sa.Column("buffer_revision", sa.Uuid(), nullable=True))
    # A service must still resolve the enrolled student's pinned version before issuing a URL.
    # This policy is the cross-org backstop; media never queries another module's tables.
    op.execute("""
        CREATE FUNCTION app.video_readable(asset uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM public.course_version_lessons vl
            JOIN public.course_versions v ON v.id = vl.version_id
            JOIN public.enrollments e ON e.course_id = v.course_id
              AND e.major_version = v.major
            WHERE vl.video_asset_id = asset
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
    op.execute("REVOKE ALL ON FUNCTION app.video_readable(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.video_readable(uuid) TO {APP_ROLE}")
    op.execute("DROP POLICY video_assets_select ON video_assets")
    policy("video_assets", "select", using=f"{EDITOR} OR app.video_readable(id)")


def downgrade() -> None:
    op.execute("DROP POLICY video_assets_select ON video_assets")
    policy("video_assets", "select", using=EDITOR)
    op.execute("DROP FUNCTION app.video_readable(uuid)")
    op.drop_column("lesson_progress", "buffer_revision")
