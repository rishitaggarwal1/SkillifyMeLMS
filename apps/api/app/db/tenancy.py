"""Row-Level Security tenant context.

RLS policies on tenant-owned tables compare `organization_id` with `app.current_org_id()`, which
reads the `app.current_org` setting. We set it with `set_config(..., is_local => true)` so it is
scoped to the current transaction: it can never leak to another request that reuses the pooled
connection, and it is safe behind a transaction-mode connection pooler (PgBouncer / RDS Proxy).

With no org set, `app.current_org_id()` is NULL and tenant-scoped policies match no rows.

`app.platform_admin` ('true' / '') carries the verified `platform_admin` realm role from the JWT; it
is read by `app.current_user_is_platform_admin()`. Only the auth layer may set it to true.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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


@asynccontextmanager
async def system_transaction(
    sessionmaker: async_sessionmaker[AsyncSession], *, organization_id: UUID | None = None
) -> AsyncIterator[AsyncSession]:
    """A transaction for background system jobs (enrollment fan-out, consumers): platform-admin
    RLS context, no acting user. Pass the organization the job works for, so the domain events it
    writes pass the outbox policy (events belong to the current org)."""
    async with sessionmaker() as session, session.begin():
        await set_tenant_context(
            session, organization_id=organization_id, user_id=None, is_platform_admin=True
        )
        yield session


@asynccontextmanager
async def independent_transaction(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    user_id: UUID | None,
    is_platform_admin: bool = False,
) -> AsyncIterator[AsyncSession]:
    """A separate transaction on `session`'s engine, with the given tenant context (RLS still
    applies). It commits on its own, so its writes survive the request transaction rolling back:
    use it to record an outcome, then raise an error to the client. Keep it short, and don't touch
    rows the request transaction has locked (it would wait on itself)."""
    async with AsyncSession(session.bind, expire_on_commit=False) as other, other.begin():
        await set_tenant_context(
            other,
            organization_id=organization_id,
            user_id=user_id,
            is_platform_admin=is_platform_admin,
        )
        yield other
