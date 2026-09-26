"""Migration helpers for Row-Level Security.

Policies are written **per operation** (SELECT / INSERT / UPDATE / DELETE), never `FOR ALL`, so
each rule is explicit and reviewable. In a migration:

    from app.db.rls import ORG_ADMIN_HERE, PLATFORM_ADMIN, enable_rls, policy

    op.create_table("batches", ...)
    enable_rls("batches")
    policy("batches", "select", using=f"{PLATFORM_ADMIN} OR {STAFF_HERE}")
    policy("batches", "insert", check=f"{PLATFORM_ADMIN} OR {ORG_ADMIN_HERE}")
    ...

The API connects as APP_ROLE, which does not own the tables and has no BYPASSRLS, so these policies
always apply to it. Helper functions (`app.current_user_has_role`, ...) are defined by the identity
migration and are the only way policies reach identity data.

Performance: wrapping a helper call whose arguments don't depend on the row in `(SELECT ...)` makes
Postgres evaluate it once per query (an InitPlan) instead of once per row.
"""

import re
from typing import Literal

from alembic import op

# Non-owner role the API connects as. Created by `python -m app.cli.db_roles` (dev/CI) or Terraform,
# never by a migration, because its password is a secret.
APP_ROLE = "skillify_app"
# Role the outbox relay connects as: may read and mark-published every outbox event, nothing else.
RELAY_ROLE = "skillify_relay"

STAFF_ROLES = "'{org_admin,instructor,lab_author}'::text[]"
ALL_ORG_ROLES = "'{org_admin,instructor,lab_author,student}'::text[]"

# Reusable policy expressions (evaluated once per query thanks to the SELECT wrappers).
PLATFORM_ADMIN = "(SELECT app.current_user_is_platform_admin())"
IN_CURRENT_ORG = "organization_id = app.current_org_id()"
ORG_ADMIN_HERE = (
    f"({IN_CURRENT_ORG} AND "
    "(SELECT app.current_user_has_role(app.current_org_id(), '{org_admin}'::text[])))"
)
STAFF_HERE = (
    f"({IN_CURRENT_ORG} AND "
    f"(SELECT app.current_user_has_role(app.current_org_id(), {STAFF_ROLES})))"
)
MEMBER_HERE = f"({IN_CURRENT_ORG} AND (SELECT app.current_user_is_member(app.current_org_id())))"

Command = Literal["select", "insert", "update", "delete"]

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ident(name: str) -> str:
    # DDL cannot take bind parameters; only allow plain, static identifiers.
    if not _IDENTIFIER.match(name):
        msg = f"Unsafe SQL identifier: {name!r}"
        raise ValueError(msg)
    return name


def enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {_ident(table)} ENABLE ROW LEVEL SECURITY")


def policy(
    table: str,
    command: Command,
    *,
    using: str | None = None,
    check: str | None = None,
    name: str | None = None,
    to: str | None = None,
) -> None:
    """Create one per-operation policy. Expressions are static SQL written in migrations."""
    if command in ("select", "delete") and (using is None or check is not None):
        msg = f"{command} policies take USING only"
        raise ValueError(msg)
    if command == "insert" and (check is None or using is not None):
        msg = "insert policies take WITH CHECK only"
        raise ValueError(msg)
    if command == "update" and (using is None or check is None):
        msg = "update policies take both USING and WITH CHECK"
        raise ValueError(msg)
    t = _ident(table)
    policy_name = _ident(name or f"{t}_{command}")
    sql = f"CREATE POLICY {policy_name} ON {t} FOR {command.upper()}"
    if to is not None:
        sql += f" TO {_ident(to)}"
    if using is not None:
        sql += f" USING ({using})"
    if check is not None:
        sql += f" WITH CHECK ({check})"
    op.execute(sql)


def enable_tenant_rls(table: str, *, column: str = "organization_id") -> None:
    """Simple tenant isolation (row visible and writable only inside its org), per operation."""
    c = _ident(column)
    expr = f"{c} = app.current_org_id()"
    enable_rls(table)
    policy(table, "select", using=expr)
    policy(table, "insert", check=expr)
    policy(table, "update", using=expr, check=expr)
    policy(table, "delete", using=expr)


def drop_policies(table: str, *names: str) -> None:
    t = _ident(table)
    for n in names or tuple(f"{t}_{c}" for c in ("select", "insert", "update", "delete")):
        op.execute(f"DROP POLICY IF EXISTS {_ident(n)} ON {t}")
    op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
