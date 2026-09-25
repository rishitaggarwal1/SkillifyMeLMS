"""Initial schema: RLS tenant-context helpers, app-role grants, transactional outbox.

Revision ID: 0001
Revises:
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.rls import APP_ROLE

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Tenant context helpers used by every RLS policy -------------------------------------
    # current_setting(..., true) returns NULL when unset; set_config(..., '') yields ''.
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute(
        """
        CREATE FUNCTION app.current_org_id() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT NULLIF(current_setting('app.current_org', true), '')::uuid $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.current_user_id() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT NULLIF(current_setting('app.current_user', true), '')::uuid $$
        """
    )

    # --- Privileges for the runtime role (DML only; no DDL, not an owner, so RLS applies) ------
    op.execute(f"GRANT USAGE ON SCHEMA app TO {APP_ROLE}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION app.current_org_id(), app.current_user_id() TO {APP_ROLE}"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    # Applies to every table/sequence later created by the migration role in this schema.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
    )

    # --- Transactional outbox -----------------------------------------------------------------
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
    )
    op.create_index(op.f("ix_outbox_events_organization_id"), "outbox_events", ["organization_id"])
    op.create_index(
        "ix_outbox_events_unpublished",
        "outbox_events",
        ["occurred_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_unpublished", table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_organization_id"), table_name="outbox_events")
    op.drop_table("outbox_events")

    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    op.execute("DROP FUNCTION IF EXISTS app.current_user_id()")
    op.execute("DROP FUNCTION IF EXISTS app.current_org_id()")
    op.execute(f"REVOKE ALL ON SCHEMA app FROM {APP_ROLE}")
    op.execute("DROP SCHEMA IF EXISTS app")
