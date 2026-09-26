"""Cross-organization isolation: a user in org A can neither read nor modify org B's data, whether
through raw SQL or through the repositories directly. Every query runs as the non-owner runtime
role with a real tenant context, exactly as the API does."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorParams
from app.db.base import new_id
from app.modules.audit.repository import AuditLogRepository
from app.modules.identity.repository import (
    BatchMemberRepository,
    BatchRepository,
    ImportJobRepository,
    InvitationRepository,
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)
from app.modules.identity.tests.conftest import World
from tests.fixtures import TenantSessionFactory

TENANT_TABLES = (
    "memberships",
    "batches",
    "batch_members",
    "invitations",
    "import_jobs",
    "import_job_errors",
    "audit_log",
    "outbox_events",
)


async def _count(session: AsyncSession, sql: str, **params: Any) -> int:
    return int(await session.scalar(text(sql), params) or 0)


async def _rowcount(session: AsyncSession, sql: str, **params: Any) -> int:
    result = await session.execute(text(sql), params)
    return int(result.rowcount)  # type: ignore[attr-defined]


async def _expect_rls_violation(session: AsyncSession, sql: str, **params: Any) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with session.begin_nested():
            await session.execute(text(sql), params)


# ---------------------------------------------------------------------------- raw SQL: read


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_org_admin_cannot_read_other_org_rows(
    world: World, tenant_session: TenantSessionFactory, table: str
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        own = await _count(s, f"SELECT count(*) FROM {table} WHERE organization_id = :o",
                           o=world.org_a.id)  # fmt: skip
        other = await _count(s, f"SELECT count(*) FROM {table} WHERE organization_id = :o",
                             o=world.org_b.id)  # fmt: skip

    assert own > 0
    assert other == 0


async def test_other_org_and_its_users_are_invisible(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        orgs = set(await s.scalars(text("SELECT id FROM organizations")))
        users = set(await s.scalars(text("SELECT id FROM users")))

    assert world.org_a.id in orgs
    assert world.org_b.id not in orgs
    assert {world.admin_a.id, world.student_a.id, world.instructor_a.id} <= users
    assert world.admin_b.id not in users
    assert world.student_b.id not in users


async def test_forged_org_context_without_membership_grants_nothing(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    # Even if app.current_org were set to org B for org A's admin, the policies still check roles.
    async with tenant_session(org=world.org_b.id, user=world.admin_a.id) as s:
        for table in TENANT_TABLES:
            if table == "outbox_events":
                continue  # outbox is scoped by org only; the API never sets an unverified org
            other = await _count(
                s, f"SELECT count(*) FROM {table} WHERE organization_id = :o", o=world.org_b.id
            )
            assert other == 0, table
        await _expect_rls_violation(
            s,
            "INSERT INTO batches (id, organization_id, name) VALUES (:id, :o, 'x')",
            id=new_id(),
            o=world.org_b.id,
        )


# ---------------------------------------------------------------------------- raw SQL: write

UPDATES = {
    "memberships": "UPDATE memberships SET role = role WHERE organization_id = :o",
    "batches": "UPDATE batches SET name = name || '!' WHERE organization_id = :o",
    "invitations": "UPDATE invitations SET status = 'revoked' WHERE organization_id = :o",
    "import_jobs": "UPDATE import_jobs SET status = 'failed' WHERE organization_id = :o",
    "organizations": "UPDATE organizations SET name = 'pwned' WHERE id = :o",
}
DELETES = {
    "memberships": "DELETE FROM memberships WHERE organization_id = :o",
    "batches": "DELETE FROM batches WHERE organization_id = :o",
    "batch_members": "DELETE FROM batch_members WHERE organization_id = :o",
    "invitations": "DELETE FROM invitations WHERE organization_id = :o",
    "import_jobs": "DELETE FROM import_jobs WHERE organization_id = :o",
    "import_job_errors": "DELETE FROM import_job_errors WHERE organization_id = :o",
    "organizations": "DELETE FROM organizations WHERE id = :o",
}


@pytest.mark.parametrize("sql", [*UPDATES.values(), *DELETES.values()], ids=[
    *(f"update-{t}" for t in UPDATES), *(f"delete-{t}" for t in DELETES)])  # fmt: skip
async def test_org_admin_cannot_modify_other_org_rows(
    world: World, tenant_session: TenantSessionFactory, sql: str
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        assert await _rowcount(s, sql, o=world.org_b.id) == 0


async def test_other_org_users_cannot_be_modified(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        updated = await _rowcount(
            s, "UPDATE users SET full_name = 'pwned' WHERE id = :u", u=world.admin_b.id
        )
        deleted = await _rowcount(s, "DELETE FROM users WHERE id = :u", u=world.admin_b.id)
    assert (updated, deleted) == (0, 0)


def _inserts(w: World) -> dict[str, tuple[str, dict[str, Any]]]:
    b = w.org_b.id
    return {
        "organizations": (
            "INSERT INTO organizations (id, name, slug) VALUES (:id, 'x', :slug)",
            {"id": new_id(), "slug": f"x-{new_id().hex[:8]}"},
        ),
        "batches": (
            "INSERT INTO batches (id, organization_id, name) VALUES (:id, :o, 'x')",
            {"id": new_id(), "o": b},
        ),
        "memberships": (
            "INSERT INTO memberships (id, user_id, organization_id, role) "
            "VALUES (:id, :u, :o, 'org_admin')",
            {"id": new_id(), "u": w.admin_a.id, "o": b},
        ),
        "batch_members": (
            "INSERT INTO batch_members (id, batch_id, organization_id, user_id) "
            "VALUES (:id, :batch, :o, :u)",
            {"id": new_id(), "batch": w.batch_b.id, "o": b, "u": w.admin_b.id},
        ),
        "invitations": (
            "INSERT INTO invitations (id, organization_id, email, roles, expires_at) "
            "VALUES (:id, :o, 'x@example.test', '{student}', now() + interval '1 day')",
            {"id": new_id(), "o": b},
        ),
        "import_jobs": (
            "INSERT INTO import_jobs (id, organization_id, file_key) VALUES (:id, :o, 'k')",
            {"id": new_id(), "o": b},
        ),
        "import_job_errors": (
            "INSERT INTO import_job_errors (id, import_job_id, organization_id, row_number, "
            "code, message) VALUES (:id, :job, :o, 1, 'c', 'm')",
            {"id": new_id(), "job": w.job_b.id, "o": b},
        ),
        "audit_log": (
            "INSERT INTO audit_log (id, organization_id, actor_user_id, action, target_type) "
            "VALUES (:id, :o, :u, 'x', 'x')",
            {"id": new_id(), "o": b, "u": w.admin_a.id},
        ),
        "outbox_events": (
            "INSERT INTO outbox_events (id, organization_id, aggregate_type, aggregate_id, "
            "event_type, payload) VALUES (:id, :o, 'x', :id, 'x', '{}')",
            {"id": new_id(), "o": b},
        ),
    }


@pytest.mark.parametrize(
    "table",
    [
        "organizations",
        "batches",
        "memberships",
        "batch_members",
        "invitations",
        "import_jobs",
        "import_job_errors",
        "audit_log",
        "outbox_events",
    ],
)
async def test_org_admin_cannot_insert_into_other_org(
    world: World, tenant_session: TenantSessionFactory, table: str
) -> None:
    sql, params = _inserts(world)[table]
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        await _expect_rls_violation(s, sql, **params)


# ---------------------------------------------------------------------------- repositories


async def test_repositories_cannot_read_other_org(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    page = CursorParams(limit=100)
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        assert await OrganizationRepository(s).get(world.org_b.id) is None
        assert await OrganizationRepository(s).get_many([world.org_b.id]) == []
        assert await UserRepository(s).get(world.admin_b.id) is None
        assert await UserRepository(s).get_by_email(world.student_b.email) is None
        assert await BatchRepository(s).get(world.batch_b.id) is None
        assert (await BatchRepository(s).list_page(world.org_b.id, page))[0] == []
        assert await MembershipRepository(s).roles_in_org(world.admin_b.id, world.org_b.id) == set()
        assert await BatchMemberRepository(s).list_user_ids(world.batch_b.id) == []
        assert not await BatchMemberRepository(s).is_member(world.batch_b.id, world.student_b.id)
        assert await InvitationRepository(s).get(world.inv_b.id) is None
        assert (await InvitationRepository(s).list_page(world.org_b.id, page))[0] == []
        assert await ImportJobRepository(s).get(world.job_b.id) is None
        assert await ImportJobRepository(s).list_errors(world.job_b.id) == []
        assert (await AuditLogRepository(s).list_page(page, organization_id=world.org_b.id))[
            0
        ] == []

        # Sanity: the same calls do see org A's data.
        assert await BatchRepository(s).get(world.batch_a1.id) is not None
        assert await ImportJobRepository(s).list_errors(world.job_a.id) != []


async def test_repositories_cannot_modify_other_org(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        assert await OrganizationRepository(s).update(world.org_b.id, {"name": "x"}) is None
        assert not await OrganizationRepository(s).delete(world.org_b.id)
        assert await UserRepository(s).update(world.admin_b.id, {"full_name": "x"}) is None
        assert await BatchRepository(s).update(world.batch_b.id, {"name": "x"}) is None
        assert not await BatchRepository(s).delete(world.batch_b.id)
        assert await InvitationRepository(s).update(world.inv_b.id, {"status": "revoked"}) is None
        assert await ImportJobRepository(s).update(world.job_b.id, {"status": "failed"}) is None
        assert (
            await MembershipRepository(s).remove(
                user_id=world.admin_b.id, organization_id=world.org_b.id
            )
            == 0
        )
        assert (
            await BatchMemberRepository(s).remove(
                batch_id=world.batch_b.id, user_ids=[world.student_b.id]
            )
            == []
        )
        assert (
            await BatchMemberRepository(s).remove_user_from_org(
                organization_id=world.org_b.id, user_id=world.student_b.id
            )
            == []
        )


async def test_repositories_cannot_write_into_other_org(
    world: World, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=world.org_a.id, user=world.admin_a.id) as s:
        writes = [
            BatchRepository(s).create(
                organization_id=world.org_b.id, name="x", description="", created_by=None
            ),
            MembershipRepository(s).add(
                user_id=world.admin_a.id,
                organization_id=world.org_b.id,
                role="org_admin",
                created_by=None,
            ),
            BatchMemberRepository(s).add_many(
                batch_id=world.batch_b.id,
                organization_id=world.org_b.id,
                user_ids=[world.student_b.id],
                added_by=None,
            ),
        ]
        for write in writes:
            with pytest.raises(DBAPIError, match="row-level security"):
                async with s.begin_nested():
                    await write
