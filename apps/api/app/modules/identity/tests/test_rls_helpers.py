"""The SQL helper functions: identity's database-level interface for RLS policies in every module
(Phase 2's course policies are built on these)."""

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.db.rls import APP_ROLE, RELAY_ROLE
from app.modules.identity.tests.conftest import World
from tests.factories import Factory
from tests.fixtures import TenantSessionFactory

HELPERS = {
    "app.current_user_is_platform_admin()": False,  # plain SQL, reads a setting only
    "app.current_user_has_role(uuid,text[])": True,
    "app.current_user_is_member(uuid)": True,
    "app.current_user_in_batch(uuid)": True,
    "app.org_is_content_publisher(uuid)": True,
    "app.user_is_member_of(uuid,uuid)": True,
    "app.user_visible_to_current_user(uuid)": True,
    "app.provision_user(uuid,text,text,text)": True,
}


async def _bool(session: AsyncSession, sql: str, **params: object) -> bool:
    return bool(await session.scalar(text(sql), params))


async def test_has_role_is_evaluated_per_organization(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    check = "SELECT app.current_user_has_role(:o, CAST(:roles AS text[]))"
    async with tenant_session(org=world.org_a.id, user=world.multi.id) as s:
        assert await _bool(s, check, o=world.org_a.id, roles=["instructor"])
        assert not await _bool(s, check, o=world.org_a.id, roles=["org_admin", "student"])
        assert await _bool(s, check, o=world.org_b.id, roles=["student"])
        assert not await _bool(s, check, o=world.org_b.id, roles=["instructor"])
        assert await _bool(s, "SELECT app.current_user_is_member(:o)", o=world.org_b.id)


async def test_has_role_is_false_without_a_user(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=None) as s:
        assert not await _bool(
            s, "SELECT app.current_user_has_role(:o, '{org_admin}')", o=world.org_a.id
        )


async def test_in_batch(world: World, tenant_session: TenantSessionFactory) -> None:
    async with tenant_session(org=world.org_a.id, user=world.student_a.id) as s:
        assert await _bool(s, "SELECT app.current_user_in_batch(:b)", b=world.batch_a1.id)
        assert not await _bool(s, "SELECT app.current_user_in_batch(:b)", b=world.batch_a2.id)


async def test_org_is_content_publisher(
    factory: Factory, tenant_session: TenantSessionFactory
) -> None:
    publisher = await factory.org(publisher=True)
    college = await factory.org()
    archived_publisher = await factory.org(publisher=True, status="archived")
    async with tenant_session(org=None, user=None) as s:
        # Callable from any context: Phase 2 policies check the *owner* org of a course.
        assert await _bool(s, "SELECT app.org_is_content_publisher(:o)", o=publisher.id)
        assert not await _bool(s, "SELECT app.org_is_content_publisher(:o)", o=college.id)
        assert not await _bool(
            s, "SELECT app.org_is_content_publisher(:o)", o=archived_publisher.id
        )
        assert not await _bool(s, "SELECT app.org_is_content_publisher(:o)", o=new_id())


async def test_platform_admin_flag(tenant_session: TenantSessionFactory) -> None:
    async with tenant_session(org=None, user=None, platform_admin=True) as s:
        assert await _bool(s, "SELECT app.current_user_is_platform_admin()")
    async with tenant_session(org=None, user=None) as s:
        assert not await _bool(s, "SELECT app.current_user_is_platform_admin()")


async def test_user_visibility(world: World, tenant_session: TenantSessionFactory) -> None:
    visible = "SELECT app.user_visible_to_current_user(:u)"
    async with tenant_session(org=world.org_a.id, user=world.instructor_a.id) as s:
        assert await _bool(s, visible, u=world.student_a.id)
        assert not await _bool(s, visible, u=world.student_b.id)
    async with tenant_session(org=world.org_a.id, user=world.student_a.id) as s:
        assert await _bool(s, visible, u=world.student_a.id)
        assert not await _bool(s, visible, u=world.student_a2.id)


@pytest.mark.parametrize(("signature", "definer"), list(HELPERS.items()))
async def test_helpers_are_hardened(
    db_session: AsyncSession, signature: str, definer: bool
) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT p.prosecdef, p.proconfig, "
                "has_function_privilege(:app, CAST(:sig AS regprocedure), 'EXECUTE') AS app_exec, "
                "has_function_privilege(:relay, CAST(:sig AS regprocedure), 'EXECUTE') "
                "AS relay_exec "
                "FROM pg_proc p WHERE p.oid = CAST(:sig AS regprocedure)"
            ),
            {"sig": signature, "app": APP_ROLE, "relay": RELAY_ROLE},
        )
    ).one()
    assert row.prosecdef is definer
    if definer:
        assert "search_path=pg_catalog, public" in (row.proconfig or [])
    assert row.app_exec is True
    assert row.relay_exec is False  # EXECUTE revoked from PUBLIC


# ---------------------------------------------------------------------------- provisioning


async def _provision(session: AsyncSession, sub: str, email: str, name: str) -> tuple[UUID, str]:
    row = (
        await session.execute(
            text("SELECT user_id, user_status FROM app.provision_user(:id, :sub, :email, :name)"),
            {"id": new_id(), "sub": sub, "email": email, "name": name},
        )
    ).one()
    return row.user_id, row.user_status


async def test_provision_creates_then_updates(tenant_session: TenantSessionFactory) -> None:
    sub = f"kc-{new_id().hex}"
    email = f"{sub}@example.test"
    async with tenant_session(org=None, user=None) as s:
        user_id, status = await _provision(s, sub, email, "First Name")
        again_id, again_status = await _provision(s, sub, "new-" + email, "")
        row = (
            await s.execute(
                text("SELECT email, full_name FROM users WHERE id = :u"), {"u": user_id}
            )
        ).one_or_none()

    assert status == again_status == "active"
    assert again_id == user_id
    # Row not visible (no tenant context as that user) -- provisioning goes through the definer.
    assert row is None


async def test_provision_activates_invited_user_and_keeps_disabled(
    factory: Factory, tenant_session: TenantSessionFactory
) -> None:
    invited = await factory.user(status="invited")
    disabled = await factory.user(status="disabled")
    async with tenant_session(org=None, user=None) as s:
        _, invited_status = await _provision(s, invited.keycloak_sub, invited.email, "")
        _, disabled_status = await _provision(s, disabled.keycloak_sub, disabled.email, "")
    assert invited_status == "active"
    assert disabled_status == "disabled"


async def test_provision_rejects_email_owned_by_another_account(
    factory: Factory, tenant_session: TenantSessionFactory
) -> None:
    existing = await factory.user()
    async with tenant_session(org=None, user=None) as s:
        with pytest.raises(IntegrityError, match="uq_users_email_lower"):
            await _provision(s, f"kc-{new_id().hex}", existing.email.upper(), "")
