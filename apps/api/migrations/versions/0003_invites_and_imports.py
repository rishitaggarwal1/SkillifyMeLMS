"""Invitations and CSV imports support.

- app.ensure_users(): bulk find-or-create users by Keycloak id for invites/imports. Org admins
  cannot see users outside their org (RLS), so this SECURITY DEFINER function does the lookup;
  it checks the caller is an org_admin of the current org (or a platform admin) itself.
- app.provision_user(): first login also marks the user's pending invitations as accepted.
- audit_log insert policy: platform admins may record entries for any organization (e.g. the org
  they just created), not only the current one.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

from app.db.rls import APP_ROLE, MEMBER_HERE, PLATFORM_ADMIN, policy

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENSURE_USERS_SIG = "app.ensure_users(uuid[], text[], text[], text[])"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION app.ensure_users(
            new_ids uuid[], subs text[], emails text[], full_names text[]
        ) RETURNS TABLE (keycloak_sub text, user_id uuid, user_status text, email_conflict boolean)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public
        AS $$
        DECLARE
          i integer;
          found_id uuid;
          found_status text;
        BEGIN
          IF NOT (
            app.current_user_is_platform_admin()
            OR app.current_user_has_role(app.current_org_id(), '{org_admin}'::text[])
          ) THEN
            RAISE EXCEPTION 'ensure_users requires org_admin in the current organization'
              USING ERRCODE = '42501';
          END IF;
          FOR i IN 1 .. coalesce(cardinality(subs), 0) LOOP
            keycloak_sub := subs[i];
            SELECT u.id, u.status INTO found_id, found_status
              FROM public.users u WHERE u.keycloak_sub = subs[i];
            IF FOUND THEN
              user_id := found_id; user_status := found_status; email_conflict := false;
            ELSIF EXISTS (SELECT 1 FROM public.users u WHERE lower(u.email) = lower(emails[i])) THEN
              -- The email belongs to a different identity: never merge accounts silently.
              user_id := NULL; user_status := NULL; email_conflict := true;
            ELSE
              INSERT INTO public.users AS u (id, keycloak_sub, email, full_name, status)
              VALUES (new_ids[i], subs[i], lower(emails[i]), coalesce(full_names[i], ''), 'invited')
              RETURNING u.id, u.status INTO found_id, found_status;
              user_id := found_id; user_status := found_status; email_conflict := false;
            END IF;
            RETURN NEXT;
          END LOOP;
        END
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_ENSURE_USERS_SIG} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_ENSURE_USERS_SIG} TO {APP_ROLE}")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.provision_user(
            new_id uuid, sub text, user_email text, user_full_name text
        ) RETURNS TABLE (user_id uuid, user_status text)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public
        AS $$
        DECLARE
          provisioned_id uuid;
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
          IF NOT FOUND THEN
            RETURN QUERY
              INSERT INTO public.users AS u
                (id, keycloak_sub, email, full_name, status, last_login_at)
              VALUES (new_id, sub, user_email, coalesce(user_full_name, ''), 'active', now())
              RETURNING u.id, u.status::text;
          END IF;
          SELECT u.id INTO provisioned_id FROM public.users u WHERE u.keycloak_sub = sub;
          UPDATE public.invitations SET status = 'accepted', updated_at = now()
            WHERE invitations.user_id = provisioned_id AND status = 'pending';
        END
        $$
        """
    )

    op.execute("DROP POLICY audit_log_insert ON audit_log")
    policy(
        "audit_log",
        "insert",
        check=f"actor_user_id = app.current_user_id() AND ({PLATFORM_ADMIN} OR {MEMBER_HERE})",
    )


def downgrade() -> None:
    op.execute("DROP POLICY audit_log_insert ON audit_log")
    policy(
        "audit_log",
        "insert",
        check="actor_user_id = app.current_user_id() AND ("
        f"(organization_id IS NULL AND {PLATFORM_ADMIN}) OR "
        f"{MEMBER_HERE} OR "
        f"(organization_id = app.current_org_id() AND {PLATFORM_ADMIN}))",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.provision_user(
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
    op.execute(f"DROP FUNCTION IF EXISTS {_ENSURE_USERS_SIG}")
