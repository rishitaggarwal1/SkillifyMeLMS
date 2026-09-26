"""Identity, organizations and access control: tables, RLS helper functions, per-operation policies,
audit log, and outbox RLS (app role scoped to its org; relay role reads everything).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.rls import (
    ALL_ORG_ROLES,
    APP_ROLE,
    IN_CURRENT_ORG,
    MEMBER_HERE,
    ORG_ADMIN_HERE,
    PLATFORM_ADMIN,
    RELAY_ROLE,
    STAFF_HERE,
    STAFF_ROLES,
    drop_policies,
    enable_rls,
    policy,
)

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_ROLES = ("org_admin", "instructor", "lab_author", "student")
_ROLE_CHECK = "role IN ('org_admin', 'instructor', 'lab_author', 'student')"


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ------------------------------------------------------------------------------ tables
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("is_content_publisher", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_organizations_status"),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="ck_organizations_slug_format"
        ),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index("ix_organizations_status", "organizations", ["status"])

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("keycloak_sub", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), server_default="", nullable=False),
        sa.Column("status", sa.String(20), server_default="invited", nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('invited', 'active', 'disabled')", name="ck_users_status"),
        sa.UniqueConstraint("keycloak_sub", name="uq_users_keycloak_sub"),
    )
    op.execute("CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))")
    op.execute("CREATE INDEX ix_users_email_trgm ON users USING gin (lower(email) gin_trgm_ops)")
    op.execute(
        "CREATE INDEX ix_users_full_name_trgm ON users USING gin (lower(full_name) gin_trgm_ops)"
    )

    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_ROLE_CHECK, name="ck_memberships_role"),
        sa.UniqueConstraint(
            "user_id", "organization_id", "role", name="uq_memberships_user_org_role"
        ),
    )
    op.create_index(
        "ix_memberships_organization_id_role", "memberships", ["organization_id", "role"]
    )
    op.create_index("ix_memberships_created_by", "memberships", ["created_by"])

    op.create_table(
        "batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), server_default="", nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        *_timestamps(),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_batches_status"),
        # Lets other tables use a composite FK (batch_id, organization_id) that guarantees a batch
        # belongs to the stated org (Phase 2's course_assignments relies on it).
        sa.UniqueConstraint("id", "organization_id", name="uq_batches_id_organization_id"),
    )
    op.create_index("ix_batches_organization_id_status", "batches", ["organization_id", "status"])
    op.create_index("ix_batches_created_by", "batches", ["created_by"])
    op.execute("CREATE UNIQUE INDEX uq_batches_org_name ON batches (organization_id, lower(name))")

    op.create_table(
        "batch_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("added_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["batches.id", "batches.organization_id"],
            ondelete="CASCADE",
            name="fk_batch_members_batch_org",
        ),
        sa.UniqueConstraint("batch_id", "user_id", name="uq_batch_members_batch_user"),
    )
    op.create_index("ix_batch_members_batch_org", "batch_members", ["batch_id", "organization_id"])
    op.create_index("ix_batch_members_user_org", "batch_members", ["user_id", "organization_id"])
    op.create_index("ix_batch_members_added_by", "batch_members", ["added_by"])

    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "batch_ids",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("invited_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')", name="ck_invitations_status"
        ),
        sa.CheckConstraint(
            f"cardinality(roles) > 0 AND roles <@ {ALL_ORG_ROLES}", name="ck_invitations_roles"
        ),
    )
    op.create_index(
        "ix_invitations_organization_id_status", "invitations", ["organization_id", "status"]
    )
    op.create_index("ix_invitations_user_id", "invitations", ["user_id"])
    op.create_index("ix_invitations_invited_by", "invitations", ["invited_by"])
    op.create_index(
        "ix_invitations_expires_at_pending",
        "invitations",
        ["expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_invitations_pending_email ON invitations "
        "(organization_id, lower(email)) WHERE status = 'pending'"
    )

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(30), server_default="queued", nullable=False),
        sa.Column("file_key", sa.String(500), nullable=False),
        sa.Column("file_name", sa.String(255), server_default="", nullable=False),
        sa.Column("total_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'completed_with_errors', 'failed')",
            name="ck_import_jobs_status",
        ),
    )
    op.execute(
        # SET NULL on batch_id only (PG15+ column list): organization_id must stay NOT NULL.
        "ALTER TABLE import_jobs ADD CONSTRAINT fk_import_jobs_batch_org "
        "FOREIGN KEY (batch_id, organization_id) REFERENCES batches (id, organization_id) "
        "ON DELETE SET NULL (batch_id)"
    )
    op.create_index("ix_import_jobs_organization_id", "import_jobs", ["organization_id", "id"])
    op.create_index("ix_import_jobs_batch_org", "import_jobs", ["batch_id", "organization_id"])
    op.create_index("ix_import_jobs_created_by", "import_jobs", ["created_by"])

    op.create_table(
        "import_job_errors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "import_job_id",
            sa.Uuid(),
            sa.ForeignKey("import_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
    )
    op.create_index(
        "ix_import_job_errors_job_row", "import_job_errors", ["import_job_id", "row_number"]
    )
    op.create_index(
        "ix_import_job_errors_organization_id", "import_job_errors", ["organization_id"]
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "actor_is_platform_admin", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("target_id", sa.String(100), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # No FKs on purpose: audit rows must survive deletion of the orgs/users they describe.
    op.create_index("ix_audit_log_organization_id_id", "audit_log", ["organization_id", "id"])
    op.create_index("ix_audit_log_actor_user_id", "audit_log", ["actor_user_id"])
    op.create_index("ix_audit_log_target", "audit_log", ["target_type", "target_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])

    # ------------------------------------------------------------------------------ helpers
    # Identity's database-level interface for RLS policies (its own and other modules'). SECURITY
    # DEFINER lets them read identity tables without recursing into those tables' RLS; the fixed
    # search_path prevents object-shadowing attacks.
    definer = "STABLE SECURITY DEFINER SET search_path = pg_catalog, public"
    op.execute(
        """
        CREATE FUNCTION app.current_user_is_platform_admin() RETURNS boolean
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT coalesce(current_setting('app.platform_admin', true), '') = 'true' $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION app.current_user_has_role(org uuid, roles text[]) RETURNS boolean
        LANGUAGE sql {definer}
        AS $$
          SELECT EXISTS (
            SELECT 1
            FROM public.memberships m
            JOIN public.organizations o ON o.id = m.organization_id
            WHERE m.user_id = app.current_user_id()
              AND m.organization_id = org
              AND m.role = ANY (roles)
              AND o.status = 'active'
          )
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION app.current_user_is_member(org uuid) RETURNS boolean
        LANGUAGE sql {definer}
        AS $$ SELECT app.current_user_has_role(org, {ALL_ORG_ROLES}) $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION app.current_user_in_batch(batch uuid) RETURNS boolean
        LANGUAGE sql {definer}
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM public.batch_members bm
            WHERE bm.batch_id = batch AND bm.user_id = app.current_user_id()
          )
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION app.org_is_content_publisher(org uuid) RETURNS boolean
        LANGUAGE sql {definer}
        AS $$
          SELECT coalesce(
            (SELECT o.is_content_publisher FROM public.organizations o
             WHERE o.id = org AND o.status = 'active'),
            false
          )
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION app.user_is_member_of(target_user uuid, org uuid) RETURNS boolean
        LANGUAGE sql {definer}
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM public.memberships m
            WHERE m.user_id = target_user AND m.organization_id = org
          )
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION app.user_visible_to_current_user(target_user uuid) RETURNS boolean
        LANGUAGE sql {definer}
        AS $$
          SELECT target_user = app.current_user_id()
            OR app.current_user_is_platform_admin()
            OR (
              app.current_user_has_role(app.current_org_id(), {STAFF_ROLES})
              AND app.user_is_member_of(target_user, app.current_org_id())
            )
        $$
        """
    )
    # Just-in-time provisioning on login. The API calls this only after validating the JWT.
    op.execute(
        """
        CREATE FUNCTION app.provision_user(
            new_id uuid, sub text, user_email text, user_full_name text
        ) RETURNS TABLE (user_id uuid, user_status text)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public
        AS $$
        BEGIN
          RETURN QUERY
            UPDATE public.users u SET
              email = user_email,
              full_name = coalesce(nullif(user_full_name, ''), u.full_name),
              status = CASE WHEN u.status = 'invited' THEN 'active' ELSE u.status END,
              last_login_at = now(),
              updated_at = now()
            WHERE u.keycloak_sub = sub
            RETURNING u.id, u.status::text;
          IF FOUND THEN
            RETURN;
          END IF;
          RETURN QUERY
            INSERT INTO public.users AS u
              (id, keycloak_sub, email, full_name, status, last_login_at)
            VALUES (new_id, sub, user_email, coalesce(user_full_name, ''), 'active', now())
            RETURNING u.id, u.status::text;
        END
        $$
        """
    )
    helper_signatures = (
        "app.current_user_is_platform_admin()",
        "app.current_user_has_role(uuid, text[])",
        "app.current_user_is_member(uuid)",
        "app.current_user_in_batch(uuid)",
        "app.org_is_content_publisher(uuid)",
        "app.user_is_member_of(uuid, uuid)",
        "app.user_visible_to_current_user(uuid)",
        "app.provision_user(uuid, text, text, text)",
    )
    for sig in helper_signatures:
        op.execute(f"REVOKE ALL ON FUNCTION {sig} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {sig} TO {APP_ROLE}")

    # ------------------------------------------------------------------------------ privileges
    # Default privileges (migration 0001) grant DML on new tables; narrow where needed.
    op.execute(f"REVOKE UPDATE ON users FROM {APP_ROLE}")
    # email / keycloak_sub change only through app.provision_user().
    op.execute(f"GRANT UPDATE (full_name, status, updated_at) ON users TO {APP_ROLE}")
    op.execute(f"REVOKE UPDATE ON batch_members FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE ON import_job_errors FROM {APP_ROLE}")
    # Append-only audit trail.
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {APP_ROLE}")

    # ------------------------------------------------------------------------------ policies
    enable_rls("organizations")
    policy("organizations", "select", using=f"{PLATFORM_ADMIN} OR app.current_user_is_member(id)")
    policy("organizations", "insert", check=PLATFORM_ADMIN)
    policy("organizations", "update", using=PLATFORM_ADMIN, check=PLATFORM_ADMIN)
    policy("organizations", "delete", using=PLATFORM_ADMIN)

    enable_rls("users")
    org_admin_anywhere_here = (
        "(SELECT app.current_user_has_role(app.current_org_id(), '{org_admin}'::text[]))"
    )
    policy("users", "select", using="app.user_visible_to_current_user(id)")
    # Org admins create (invited) users when inviting / importing.
    policy("users", "insert", check=f"{PLATFORM_ADMIN} OR {org_admin_anywhere_here}")
    self_or_pa = f"id = app.current_user_id() OR {PLATFORM_ADMIN}"
    policy("users", "update", using=self_or_pa, check=self_or_pa)
    policy("users", "delete", using=PLATFORM_ADMIN)

    enable_rls("memberships")
    policy(
        "memberships",
        "select",
        using=f"user_id = app.current_user_id() OR {PLATFORM_ADMIN} OR {STAFF_HERE}",
    )
    admin_write = f"{PLATFORM_ADMIN} OR {ORG_ADMIN_HERE}"
    policy("memberships", "insert", check=admin_write)
    policy("memberships", "update", using=admin_write, check=admin_write)
    policy("memberships", "delete", using=admin_write)

    enable_rls("batches")
    policy(
        "batches",
        "select",
        using=f"{PLATFORM_ADMIN} OR {STAFF_HERE} OR "
        f"({IN_CURRENT_ORG} AND app.current_user_in_batch(id))",
    )
    policy("batches", "insert", check=admin_write)
    policy("batches", "update", using=admin_write, check=admin_write)
    policy("batches", "delete", using=admin_write)

    enable_rls("batch_members")
    policy(
        "batch_members",
        "select",
        using=f"{PLATFORM_ADMIN} OR {STAFF_HERE} OR "
        f"({IN_CURRENT_ORG} AND user_id = app.current_user_id())",
    )
    policy(
        "batch_members",
        "insert",
        check=f"({admin_write}) AND app.user_is_member_of(user_id, organization_id)",
    )
    policy("batch_members", "delete", using=admin_write)  # no UPDATE: rows are immutable

    for table in ("invitations", "import_jobs"):
        enable_rls(table)
        policy(table, "select", using=admin_write)
        policy(table, "insert", check=admin_write)
        policy(table, "update", using=admin_write, check=admin_write)
        policy(table, "delete", using=admin_write)
    enable_rls("import_job_errors")
    policy("import_job_errors", "select", using=admin_write)
    policy("import_job_errors", "insert", check=admin_write)
    policy("import_job_errors", "delete", using=admin_write)

    enable_rls("audit_log")
    policy("audit_log", "select", using=admin_write)
    policy(
        "audit_log",
        "insert",
        check="actor_user_id = app.current_user_id() AND ("
        f"(organization_id IS NULL AND {PLATFORM_ADMIN}) OR "
        f"{MEMBER_HERE} OR "
        f"({IN_CURRENT_ORG} AND {PLATFORM_ADMIN}))",
    )

    # Outbox: the app role writes/reads only its current org's events; the relay reads all.
    enable_rls("outbox_events")
    outbox_scope = f"{IN_CURRENT_ORG} OR (organization_id IS NULL AND {PLATFORM_ADMIN})"
    policy("outbox_events", "select", using=outbox_scope, to=APP_ROLE)
    policy("outbox_events", "insert", check=outbox_scope, to=APP_ROLE)
    op.execute(f"REVOKE UPDATE, DELETE ON outbox_events FROM {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {RELAY_ROLE}")
    op.execute(f"GRANT SELECT, UPDATE (published_at) ON outbox_events TO {RELAY_ROLE}")
    policy(
        "outbox_events", "select", using="true", to=RELAY_ROLE, name="outbox_events_relay_select"
    )
    policy(
        "outbox_events",
        "update",
        using="true",
        check="true",
        to=RELAY_ROLE,
        name="outbox_events_relay_update",
    )


def downgrade() -> None:
    drop_policies(
        "outbox_events",
        "outbox_events_select",
        "outbox_events_insert",
        "outbox_events_relay_select",
        "outbox_events_relay_update",
    )
    op.execute(f"REVOKE ALL ON outbox_events FROM {RELAY_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {RELAY_ROLE}")
    op.execute(f"GRANT UPDATE, DELETE ON outbox_events TO {APP_ROLE}")

    for table in (
        "audit_log",
        "import_job_errors",
        "import_jobs",
        "invitations",
        "batch_members",
        "batches",
        "memberships",
        "users",
        "organizations",
    ):
        op.drop_table(table)

    # Functions last: policies on the tables above referenced them.
    for fn in (
        "app.provision_user(uuid, text, text, text)",
        "app.user_visible_to_current_user(uuid)",
        "app.user_is_member_of(uuid, uuid)",
        "app.org_is_content_publisher(uuid)",
        "app.current_user_in_batch(uuid)",
        "app.current_user_is_member(uuid)",
        "app.current_user_has_role(uuid, text[])",
        "app.current_user_is_platform_admin()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
