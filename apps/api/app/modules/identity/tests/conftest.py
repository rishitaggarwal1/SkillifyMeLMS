"""A two-org world shared by the identity RLS tests (created once per module via the owner role)."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import new_id
from app.modules.identity.models import Batch, ImportJob, Invitation, Organization, User
from tests.factories import Factory


@dataclass
class World:
    org_a: Organization
    org_b: Organization
    admin_a: User
    instructor_a: User
    student_a: User  # in batch_a1
    student_a2: User  # in batch_a2
    admin_b: User
    student_b: User  # in batch_b
    multi: User  # instructor in A, student in B (in batch_b)
    platform_admin: User  # no memberships; platform role comes from the JWT
    batch_a1: Batch
    batch_a2: Batch
    batch_b: Batch
    inv_a: Invitation
    inv_b: Invitation
    job_a: ImportJob
    job_b: ImportJob
    audit_a: UUID
    audit_b: UUID
    event_a: UUID
    event_b: UUID


async def _outbox_event(sessionmaker: async_sessionmaker[AsyncSession], org: UUID) -> UUID:
    event_id = new_id()
    async with sessionmaker() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO outbox_events (id, organization_id, aggregate_type, aggregate_id, "
                "event_type, payload) VALUES (:id, :org, 'probe', :agg, 'probe.created', '{}')"
            ),
            {"id": event_id, "org": org, "agg": new_id()},
        )
    return event_id


@pytest.fixture(scope="module")
async def world(owner_sessionmaker: async_sessionmaker[AsyncSession]) -> World:
    f = Factory(owner_sessionmaker)
    org_a, org_b = await f.org(name="College A"), await f.org(name="College B")
    admin_a = await f.member(org_a, "org_admin")
    instructor_a = await f.member(org_a, "instructor")
    student_a = await f.member(org_a, "student")
    student_a2 = await f.member(org_a, "student")
    admin_b = await f.member(org_b, "org_admin")
    student_b = await f.member(org_b, "student")
    multi = await f.member(org_a, "instructor")
    await f.member(org_b, "student", user=multi)
    platform_admin = await f.user()
    batch_a1, batch_a2, batch_b = await f.batch(org_a), await f.batch(org_a), await f.batch(org_b)
    await f.add_to_batch(batch_a1, student_a)
    await f.add_to_batch(batch_a2, student_a2)
    await f.add_to_batch(batch_b, student_b, multi)
    return World(
        org_a=org_a,
        org_b=org_b,
        admin_a=admin_a,
        instructor_a=instructor_a,
        student_a=student_a,
        student_a2=student_a2,
        admin_b=admin_b,
        student_b=student_b,
        multi=multi,
        platform_admin=platform_admin,
        batch_a1=batch_a1,
        batch_a2=batch_a2,
        batch_b=batch_b,
        inv_a=await f.invitation(org_a),
        inv_b=await f.invitation(org_b),
        job_a=await f.import_job(org_a, batch=batch_a1),
        job_b=await f.import_job(org_b, batch=batch_b),
        audit_a=await f.audit(org_a, admin_a),
        audit_b=await f.audit(org_b, admin_b),
        event_a=await _outbox_event(owner_sessionmaker, org_a.id),
        event_b=await _outbox_event(owner_sessionmaker, org_b.id),
    )


@dataclass
class OrgSetup:
    """A fresh org with one user per role and a batch (the student is in it)."""

    org: Organization
    admin: User
    instructor: User
    lab_author: User
    student: User
    batch: Batch
    auth: Callable[..., dict[str, str]]

    def h(self, user: User) -> dict[str, str]:
        """Auth headers for `user` acting in this org."""
        return self.auth(user, org=self.org.id)


@pytest.fixture
async def org_setup(factory: Factory, auth_headers: Callable[..., dict[str, str]]) -> OrgSetup:
    org = await factory.org()
    student = await factory.member(org, "student")
    batch = await factory.batch(org)
    await factory.add_to_batch(batch, student)
    return OrgSetup(
        org=org,
        admin=await factory.member(org, "org_admin"),
        instructor=await factory.member(org, "instructor"),
        lab_author=await factory.member(org, "lab_author"),
        student=student,
        batch=batch,
        auth=auth_headers,
    )
