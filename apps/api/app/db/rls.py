"""Migration helpers for Row-Level Security on tenant-owned tables.

In a migration that creates a tenant-owned table:

    from app.db.rls import enable_tenant_rls
    ...
    op.create_table("courses", ...)
    enable_tenant_rls("courses")

The API connects as APP_ROLE, which does not own the tables, so these policies always apply to it.
"""

import re

from alembic import op

# Non-owner role the API connects as. Created by infra (docker init script / Terraform), not here,
# because its password is a secret.
APP_ROLE = "skillify_app"

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ident(name: str) -> str:
    # DDL cannot take bind parameters; only allow plain, static identifiers.
    if not _IDENTIFIER.match(name):
        msg = f"Unsafe SQL identifier: {name!r}"
        raise ValueError(msg)
    return name


def enable_tenant_rls(table: str, *, column: str = "organization_id") -> None:
    t, c = _ident(table), _ident(column)
    op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {t} "
        f"USING ({c} = app.current_org_id()) "
        f"WITH CHECK ({c} = app.current_org_id())"
    )


def disable_tenant_rls(table: str) -> None:
    t = _ident(table)
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
