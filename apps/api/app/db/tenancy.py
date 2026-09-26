"""Row-Level Security tenant context.

RLS policies on tenant-owned tables compare `organization_id` with `app.current_org_id()`, which
reads the `app.current_org` setting. We set it with `set_config(..., is_local => true)` so it is
scoped to the current transaction: it can never leak to another request that reuses the pooled
connection, and it is safe behind a transaction-mode connection pooler (PgBouncer / RDS Proxy).

With no org set, `app.current_org_id()` is NULL and tenant-scoped policies match no rows.

`app.platform_admin` ('true' / '') carries the verified `platform_admin` realm role from the JWT; it
is read by `app.current_user_is_platform_admin()`. Only the auth layer may set it to true.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant_context(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    user_id: UUID | None,
    is_platform_admin: bool = False,
) -> None:
    if not session.in_transaction():
        msg = "Tenant context must be set inside a transaction (it is transaction-scoped)."
        raise RuntimeError(msg)
    await session.execute(
        select(
            func.set_config("app.current_org", str(organization_id or ""), True),
            func.set_config("app.current_user", str(user_id or ""), True),
            func.set_config("app.platform_admin", "true" if is_platform_admin else "", True),
        )
    )
