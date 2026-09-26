"""Within an organization, what each role can see and change (RLS, per operation)."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.modules.identity.tests.conftest import World
from tests.factories import Factory
from tests.fixtures import TenantSessionFactory


async def _ids(session: AsyncSession, sql: str, **params: Any) -> set[Any]:
    return set(await session.scalars(text(sql), params))


async def _count(session: AsyncSession, sql: str, **params: Any) -> int:
    return int(await session.scalar(text(sql), params) or 0)


async def _fails(session: AsyncSession, sql: str, match: str, **params: Any) -> None:
    with pytest.raises(DBAPIError, match=match):
        async with session.begin_nested():
            await session.execute(text(sql), params)


# ---------------------------------------------------------------------------- students


async def test_student_sees_only_own_rows(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.student_a.id) as s:
        memberships = await _ids(s, "SELECT user_id FROM memberships")
        batches = await _ids(s, "SELECT id FROM batches")
        batch_members = await _ids(s, "SELECT user_id FROM batch_members")
        users = await _ids(s, "SELECT id FROM users")
        admin_only = [
            await _count(s, f"SELECT count(*) FROM {t}")
            for t in ("invitations", "import_jobs", "import_job_errors", "audit_log")
        ]

    assert memberships == {world.student_a.id}
    assert batches == {world.batch_a1.id}  # not batch_a2, which they are not in
    assert batch_members == {world.student_a.id}
    assert users == {world.student_a.id}
    assert admin_only == [0, 0, 0, 0]


async def test_student_cannot_write(world: World, tenant_session: TenantSessionFactory) -> None:
    a = world.org_a.id
    async with tenant_session(org=a, user=world.student_a.id) as s:
        await _fails(
            s,
            "INSERT INTO batches (id, organization_id, name) VALUES (:id, :o, 'x')",
            "row-level security",
            id=new_id(),
            o=a,
        )
        await _fails(
            s,
            "INSERT INTO memberships (id, user_id, organization_id, role) "
            "VALUES (:id, :u, :o, 'org_admin')",
            "row-level security",
            id=new_id(),
            u=world.student_a.id,
            o=a,
        )
        await _fails(
            s,
            "INSERT INTO batch_members (id, batch_id, organization_id, user_id) "
            "VALUES (:id, :b, :o, :u)",
            "row-level security",
            id=new_id(),
            b=world.batch_a2.id,
            o=a,
            u=world.student_a.id,
        )
        deleted = await s.execute(
            text("DELETE FROM batch_members WHERE user_id = :u"), {"u": world.student_a.id}
        )
        assert deleted.rowcount == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------- instructors


async def test_instructor_reads_org_roster_but_not_admin_data(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.instructor_a.id) as s:
        batches = await _ids(s, "SELECT id FROM batches")
        users = await _ids(s, "SELECT id FROM users")
        roster = await _ids(s, "SELECT user_id FROM batch_members")
        invitations = await _count(s, "SELECT count(*) FROM invitations")
        audit = await _count(s, "SELECT count(*) FROM audit_log")
        await _fails(
            s,
            "INSERT INTO batches (id, organization_id, name) VALUES (:id, :o, 'x')",
            "row-level security",
            id=new_id(),
            o=world.org_a.id,
        )

    assert {world.batch_a1.id, world.batch_a2.id} <= batches
    assert world.batch_b.id not in batches
    assert {world.student_a.id, world.student_a2.id, world.admin_a.id} <= users
    assert world.student_b.id not in users
    assert {world.student_a.id, world.student_a2.id} <= roster
    assert (invitations, audit) == (0, 0)


# ---------------------------------------------------------------------------- org admins


async def test_org_admin_manages_own_org(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    a = world.org_a.id
    batch_id = new_id()
    async with tenant_session(org=a, user=world.admin_a.id) as s:
        await s.execute(
            text("INSERT INTO batches (id, organization_id, name) VALUES (:id, :o, :n)"),
            {"id": batch_id, "o": a, "n": f"New {batch_id.hex[:6]}"},
        )
        await s.execute(
            text(
                "INSERT INTO batch_members (id, batch_id, organization_id, user_id) "
                "VALUES (:id, :b, :o, :u)"
            ),
            {"id": new_id(), "b": batch_id, "o": a, "u": world.student_a2.id},
        )
        renamed = await s.execute(
            text("UPDATE batches SET name = name || '!' WHERE id = :b"), {"b": batch_id}
        )
        invitations = await _count(s, "SELECT count(*) FROM invitations")
        audit = await _count(s, "SELECT count(*) FROM audit_log")

    assert renamed.rowcount == 1  # type: ignore[attr-defined]
    assert invitations >= 1
    assert audit >= 1


async def test_org_admin_cannot_add_non_member_to_batch(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        # student_b belongs to org B only.
        await _fails(
            s,
            "INSERT INTO batch_members (id, batch_id, organization_id, user_id) "
            "VALUES (:id, :b, :o, :u)",
            "row-level security",
            id=new_id(),
            b=world.batch_a1.id,
            o=world.org_a.id,
            u=world.student_b.id,
        )


async def test_batch_must_belong_to_stated_org(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    # Claiming org A for org B's batch trips the composite foreign key, independent of RLS.
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        with pytest.raises(IntegrityError, match="fk_batch_members_batch_org"):
            async with s.begin_nested():
                await s.execute(
                    text(
                        "INSERT INTO batch_members (id, batch_id, organization_id, user_id) "
                        "VALUES (:id, :b, :o, :u)"
                    ),
                    {"id": new_id(), "b": world.batch_b.id, "o": world.org_a.id,
                     "u": world.student_a.id},
                )  # fmt: skip


async def test_org_admin_cannot_create_or_rename_organizations(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        await _fails(
            s,
            "INSERT INTO organizations (id, name, slug) VALUES (:id, 'x', :slug)",
            "row-level security",
            id=new_id(),
            slug=f"x-{new_id().hex[:8]}",
        )
        renamed = await s.execute(
            text("UPDATE organizations SET name = 'x' WHERE id = :o"), {"o": world.org_a.id}
        )
        assert renamed.rowcount == 0  # type: ignore[attr-defined]


async def test_archived_org_grants_no_roles(
    factory: Factory, tenant_session: TenantSessionFactory
) -> None:
    org = await factory.org(status="archived")
    admin = await factory.member(org, "org_admin")
    await factory.batch(org)
    async with tenant_session(org=org.id, user=admin.id) as s:
        assert await _count(s, "SELECT count(*) FROM batches") == 0
        assert await _count(s, "SELECT count(*) FROM organizations WHERE id = :o", o=org.id) == 0


# ---------------------------------------------------------------------------- multi-org users


async def test_roles_are_per_organization(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    # `multi` is an instructor in A and a student in B.
    async with tenant_session(org=world.org_a.id, user=world.multi.id) as s:
        a_batches = await _ids(s, "SELECT id FROM batches")
        a_users = await _ids(s, "SELECT id FROM users")
        own_orgs = await _ids(s, "SELECT organization_id FROM memberships WHERE user_id = :u",
                              u=world.multi.id)  # fmt: skip
    async with tenant_session(org=world.org_b.id, user=world.multi.id) as s:
        b_batches = await _ids(s, "SELECT id FROM batches")
        b_users = await _ids(s, "SELECT id FROM users")

    assert {world.batch_a1.id, world.batch_a2.id} <= a_batches  # staff in A: all batches
    assert world.student_a.id in a_users
    assert own_orgs == {world.org_a.id, world.org_b.id}  # the org switcher needs this
    assert b_batches == {world.batch_b.id}  # student in B: own batch only
    assert b_users == {world.multi.id}


# ---------------------------------------------------------------------------- platform admins


async def test_platform_admin_sees_and_manages_all_orgs(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=None, user=world.platform_admin.id, platform_admin=True) as s:
        orgs = await _ids(s, "SELECT id FROM organizations")
        audit = await _ids(s, "SELECT organization_id FROM audit_log")
        await s.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'New', :slug)"),
            {"id": new_id(), "slug": f"new-{new_id().hex[:8]}"},
        )

    assert {world.org_a.id, world.org_b.id} <= orgs
    assert {world.org_a.id, world.org_b.id} <= audit


async def test_platform_flag_is_not_inferred_from_membership(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    # The same user without the verified flag gets nothing.
    async with tenant_session(org=None, user=world.platform_admin.id) as s:
        assert await _count(s, "SELECT count(*) FROM organizations") == 0


# ---------------------------------------------------------------------------- column privileges


async def test_users_email_and_sub_are_not_updatable(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        for column in ("email", "keycloak_sub"):
            await _fails(
                s,
                f"UPDATE users SET {column} = {column} WHERE id = :u",
                "permission denied",
                u=world.admin_a.id,
            )
        renamed = await s.execute(
            text("UPDATE users SET full_name = full_name WHERE id = :u"), {"u": world.admin_a.id}
        )
        other = await s.execute(
            text("UPDATE users SET full_name = 'x' WHERE id = :u"), {"u": world.student_a.id}
        )
    assert renamed.rowcount == 1  # type: ignore[attr-defined]
    assert other.rowcount == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------- audit log


async def test_audit_log_is_append_only(world: World, tenant_session: TenantSessionFactory) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        for sql in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
            await _fails(s, sql, "permission denied")


async def test_any_member_can_append_audit_rows_as_themselves_only(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    insert = (
        "INSERT INTO audit_log (id, organization_id, actor_user_id, action, target_type) "
        "VALUES (:id, :o, :actor, 'student.action', 'x')"
    )
    async with tenant_session(org=world.org_a.id, user=world.student_a.id) as s:
        await s.execute(text(insert), {"id": new_id(), "o": world.org_a.id,
                                       "actor": world.student_a.id})  # fmt: skip
        await _fails(s, insert, "row-level security", id=new_id(), o=world.org_a.id,
                     actor=world.admin_a.id)  # fmt: skip
        # ...but cannot read the audit log back.
        assert await _count(s, "SELECT count(*) FROM audit_log") == 0


# ---------------------------------------------------------------------------- outbox


async def test_outbox_is_scoped_to_current_org(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        visible = await _ids(s, "SELECT id FROM outbox_events")
        await _fails(s, "UPDATE outbox_events SET published_at = now()", "permission denied")
    assert world.event_a in visible
    assert world.event_b not in visible
