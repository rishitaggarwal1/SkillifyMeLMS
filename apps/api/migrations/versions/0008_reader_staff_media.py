"""Staff of an org that reads a course may open its lesson content (videos, PDFs, images).

Read access means the content, not only the outline: an `org_admin` or `instructor` acting in an
org the course is assigned to (the reader rule in `app.course_readable`) may play the videos and
download the files of any published version of that course. Students keep the enrollment rule:
their pinned major's latest minor, with a current batch assignment.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

STUDENT = """
          EXISTS (
            SELECT 1 FROM public.course_version_lessons vl
            JOIN public.course_versions v ON v.id = vl.version_id
            JOIN public.enrollments e ON e.course_id = v.course_id
              AND e.major_version = v.major
            WHERE {match}
              AND e.organization_id = app.current_org_id()
              AND e.user_id = app.current_user_id() AND e.status = 'active'
              AND app.course_readable(e.course_id)
              AND NOT EXISTS (
                SELECT 1 FROM public.course_versions newer
                WHERE newer.course_id = v.course_id AND newer.major = v.major
                  AND newer.minor > v.minor
              )
          )"""

STAFF = """
          (app.current_user_has_role(app.current_org_id(), '{{org_admin,instructor}}'::text[])
           AND EXISTS (
            SELECT 1 FROM public.course_version_lessons vl
            WHERE {match} AND app.course_readable(vl.course_id)
          ))"""


def _function(name: str, arg: str, match: str, *, staff: bool) -> str:
    body = STUDENT.format(match=match)
    if staff:
        body += "\n          OR" + STAFF.format(match=match)
    return f"""
        CREATE OR REPLACE FUNCTION app.{name}({arg} uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
        AS $$
          SELECT {body}
        $$
    """


VIDEO_MATCH = "vl.video_asset_id = asset"
FILE_MATCH = "vl.file_ids @> ARRAY[file]"


def upgrade() -> None:
    # CREATE OR REPLACE keeps the grants (EXECUTE to the app role only) from 0006 and 0007.
    op.execute(_function("video_readable", "asset", VIDEO_MATCH, staff=True))
    op.execute(_function("file_readable", "file", FILE_MATCH, staff=True))


def downgrade() -> None:
    op.execute(_function("video_readable", "asset", VIDEO_MATCH, staff=False))
    op.execute(_function("file_readable", "file", FILE_MATCH, staff=False))
