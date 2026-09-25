"""Row-Level Security foundation: the runtime role cannot bypass RLS, and set_tenant_context scopes
visibility to one organization for the current transaction only."""

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.db.tenancy import set_tenant_context

ORG_A = uuid7()
ORG_B = uuid7()


async def test_runtime_role_cannot_bypass_rls(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    ).one()

    assert row.rolsuper is False
    assert row.rolbypassrls is False


async def _create_probe_table(session: AsyncSession) -> None:
    # A temp table owned by the runtime role; FORCE makes RLS apply to its owner too, mirroring a
    # real tenant table (owned by the migration role) as seen by the runtime role.
    await session.execute(
        text(
            "CREATE TEMP TABLE rls_probe (organization_id uuid NOT NULL, body text) ON COMMIT DROP"
        )
    )
    await session.execute(text("ALTER TABLE rls_probe ENABLE ROW LEVEL SECURITY"))
    await session.execute(text("ALTER TABLE rls_probe FORCE ROW LEVEL SECURITY"))
    await session.execute(
        text(
            "CREATE POLICY tenant_isolation ON rls_probe "
            "USING (organization_id = app.current_org_id()) "
            "WITH CHECK (organization_id = app.current_org_id())"
        )
    )


async def _visible_bodies(session: AsyncSession) -> list[str]:
    result = await session.execute(text("SELECT body FROM rls_probe ORDER BY body"))
    return list(result.scalars())


async def test_rls_isolates_tenants(db_session: AsyncSession) -> None:
    await _create_probe_table(db_session)
    insert = text("INSERT INTO rls_probe (organization_id, body) VALUES (:org, :body)")

    await set_tenant_context(db_session, organization_id=ORG_A, user_id=uuid7())
    await db_session.execute(insert, {"org": ORG_A, "body": "a1"})
    await set_tenant_context(db_session, organization_id=ORG_B, user_id=uuid7())
    await db_session.execute(insert, {"org": ORG_B, "body": "b1"})

    await set_tenant_context(db_session, organization_id=ORG_A, user_id=None)
    assert await _visible_bodies(db_session) == ["a1"]

    await set_tenant_context(db_session, organization_id=ORG_B, user_id=None)
    assert await _visible_bodies(db_session) == ["b1"]

    # No tenant context: nothing is visible.
    await set_tenant_context(db_session, organization_id=None, user_id=None)
    assert await _visible_bodies(db_session) == []


async def test_rls_blocks_writes_into_another_tenant(db_session: AsyncSession) -> None:
    await _create_probe_table(db_session)
    await set_tenant_context(db_session, organization_id=ORG_A, user_id=None)

    with pytest.raises(DBAPIError, match="row-level security"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("INSERT INTO rls_probe (organization_id, body) VALUES (:org, 'x')"),
                {"org": ORG_B},
            )


async def test_tenant_context_is_transaction_scoped(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        async with session.begin():
            await set_tenant_context(session, organization_id=ORG_A, user_id=None)
            inside = await session.scalar(text("SELECT app.current_org_id()"))
        async with session.begin():
            after = await session.scalar(text("SELECT app.current_org_id()"))

    assert inside == ORG_A
    assert after is None


async def test_tenant_context_requires_a_transaction(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        with pytest.raises(RuntimeError, match="inside a transaction"):
            await set_tenant_context(session, organization_id=ORG_A, user_id=None)
