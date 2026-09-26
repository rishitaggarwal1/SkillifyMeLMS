"""The identity service interface Phase 2 (enrollments) builds on."""

from uuid import UUID

from app.core.pagination import CursorParams
from app.modules.identity import service
from app.modules.identity.tests.conftest import OrgSetup
from tests.factories import Factory
from tests.fixtures import TenantSessionFactory


async def test_iter_batch_student_ids_pages_through_students_only(
    org_setup: OrgSetup, factory: Factory, tenant_session: TenantSessionFactory
) -> None:
    students = [
        org_setup.student,
        *[await factory.member(org_setup.org, "student") for _ in range(4)],
    ]
    await factory.add_to_batch(org_setup.batch, *students[1:], org_setup.instructor)

    async with tenant_session(org=org_setup.org.id, user=org_setup.admin.id) as s:
        pages = [
            page
            async for page in service.iter_batch_student_ids(s, org_setup.batch.id, page_size=2)
        ]

    flat: list[UUID] = [uid for page in pages for uid in page]
    assert [len(p) for p in pages] == [2, 2, 1]
    assert sorted(flat) == sorted(u.id for u in students)  # instructor excluded
    assert flat == sorted(flat)  # keyset order


async def test_batch_belongs_to_org_and_active_listing(
    org_setup: OrgSetup, factory: Factory, tenant_session: TenantSessionFactory
) -> None:
    foreign = await factory.batch(await factory.org())
    async with tenant_session(org=org_setup.org.id, user=org_setup.admin.id) as s:
        assert await service.batch_belongs_to_org(s, org_setup.batch.id, org_setup.org.id)
        assert not await service.batch_belongs_to_org(s, foreign.id, org_setup.org.id)
        batches, _ = await service.list_org_batches(s, org_setup.org.id, CursorParams(limit=50))
        batch_ids = [b.id for b in batches]
    assert batch_ids == [org_setup.batch.id]
