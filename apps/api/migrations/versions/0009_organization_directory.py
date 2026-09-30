"""Organization directory for content publishers (who they can assign courses to).

`organizations` stays readable only by members (and platform admins). Publishers need to find the
orgs they grant courses to, so `app.organization_directory` returns just `id` and `name` of active
organizations, and only when the caller is a platform admin or an `org_admin`/`instructor` acting
in an active content-publisher organization. Everyone else gets no rows.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

from app.db.rls import APP_ROLE

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

SIGNATURE = "app.organization_directory(text, uuid[], text, uuid, integer)"


def upgrade() -> None:
    op.execute("CREATE INDEX ix_organizations_lower_name_id ON organizations (lower(name), id)")
    op.execute("""
        CREATE FUNCTION app.organization_directory(
          search text, only_ids uuid[], after_name text, after_id uuid, page_size integer
        ) RETURNS TABLE (id uuid, name text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
        AS $$
          SELECT o.id, o.name::text
          FROM public.organizations o
          WHERE o.status = 'active'
            AND (
              app.current_user_is_platform_admin()
              OR (app.org_is_content_publisher(app.current_org_id())
                  AND app.current_user_has_role(
                        app.current_org_id(), '{org_admin,instructor}'::text[]))
            )
            -- Plain substring match: search text is never treated as a LIKE pattern.
            AND (search IS NULL OR strpos(lower(o.name), lower(search)) > 0)
            AND (only_ids IS NULL OR o.id = ANY (only_ids))
            AND (after_name IS NULL OR (lower(o.name), o.id) > (lower(after_name), after_id))
          ORDER BY lower(o.name), o.id
          LIMIT least(greatest(page_size, 1), 100)
        $$
    """)
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {SIGNATURE}")
    op.execute("DROP INDEX ix_organizations_lower_name_id")
