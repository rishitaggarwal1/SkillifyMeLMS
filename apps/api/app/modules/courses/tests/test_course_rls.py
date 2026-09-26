"""Course ownership and sharing, enforced by RLS (CLAUDE.md "Content ownership & sharing").

Required by the Phase 2 done-when:
- an assigned org cannot edit
- an unassigned org cannot see
- a batch assignment limits visibility to that batch's students
- org admins cannot widen an assignment
- students cannot see courses not assigned to their batch
"""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.modules.identity.models import User
from tests.content_world import ContentWorld
from tests.fixtures import TenantSessionFactory

COURSE_TABLES = ("courses", "course_versions", "course_version_lessons")
DRAFT_TABLES = ("course_modules", "lessons")


async def _ids(s: AsyncSession, sql: str, **params: Any) -> set[UUID]:
    return set(await s.scalars(text(sql), params))


async def _visible_courses(s: AsyncSession) -> set[UUID]:
    return await _ids(s, "SELECT id FROM courses")


async def _rowcount(s: AsyncSession, sql: str, **params: Any) -> int:
    return int((await s.execute(text(sql), params)).rowcount)  # type: ignore[attr-defined]


async def _fails(s: AsyncSession, sql: str, **params: Any) -> None:
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await s.execute(text(sql), params)


def _as(tenant_session: TenantSessionFactory, user: User, org: Any, **kw: Any) -> Any:
    return tenant_session(org=org.id, user=user.id, **kw)


# --- editors


async def test_owner_editors_see_and_edit_drafts(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    for editor in (cw.p_author, cw.p_admin):
        async with _as(tenant_session, editor, cw.p) as s:
            assert {cw.k.id, cw.k2.id, cw.k3.id, cw.k4.id} <= await _visible_courses(s)
            assert cw.k_lesson.id in await _ids(s, "SELECT id FROM lessons")
            assert (
                await _rowcount(
                    s, "UPDATE courses SET title = title || '!' WHERE id = :c", c=cw.k.id
                )
                == 1
            )
            assert (
                await _rowcount(s, "UPDATE lessons SET title = 'x' WHERE id = :l", l=cw.k_lesson.id)
                == 1
            )


async def test_owner_org_non_editors_cannot_edit(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    for user in (cw.p_lab, cw.p_student):
        async with _as(tenant_session, user, cw.p) as s:
            assert (
                await _rowcount(s, "UPDATE courses SET title = 'x' WHERE id = :c", c=cw.k.id) == 0
            )
            assert await _ids(s, "SELECT id FROM lessons") == set()


# --- assigned org cannot edit


async def test_assigned_org_cannot_edit(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    for user in (cw.c_admin, cw.c_instructor):
        async with _as(tenant_session, user, cw.c) as s:
            assert cw.k.id in await _visible_courses(s)  # it can read it...
            updates = [
                ("UPDATE courses SET title = 'pwned' WHERE id = :c", {"c": cw.k.id}),
                ("DELETE FROM courses WHERE id = :c", {"c": cw.k.id}),
                ("UPDATE lessons SET title = 'pwned' WHERE id = :l", {"l": cw.k_lesson.id}),
                ("DELETE FROM lessons WHERE id = :l", {"l": cw.k_lesson.id}),
            ]
            for sql, params in updates:
                assert await _rowcount(s, sql, **params) == 0, sql  # ...but not change it
            # ...nor see the drafts
            for table in DRAFT_TABLES:
                assert (
                    await _ids(s, f"SELECT course_id FROM {table} WHERE course_id = :c", c=cw.k.id)
                    == set()
                )
            await _fails(
                s,
                "INSERT INTO course_modules (id, course_id, organization_id, title, position) "
                "VALUES (:id, :c, :o, 'x', 99)",
                id=new_id(), c=cw.k.id, o=cw.p.id,
            )  # fmt: skip
            await _fails(
                s,
                "INSERT INTO course_versions (id, course_id, organization_id, major, minor, "
                "release_type, title, snapshot) VALUES (:id, :c, :o, 9, 0, 'major', 'x', '{}')",
                id=new_id(), c=cw.k.id, o=cw.p.id,
            )  # fmt: skip


# --- unassigned org cannot see


async def test_unassigned_org_cannot_see(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    all_courses = {cw.k.id, cw.k2.id, cw.k3.id, cw.k4.id, cw.q.id}
    for user in (cw.o_admin, cw.o_student):
        async with _as(tenant_session, user, cw.o) as s:
            assert await _visible_courses(s) & all_courses == set()
            for table in ("course_versions", "course_version_lessons", "course_assignments"):
                visible = await _ids(s, f"SELECT course_id FROM {table}")
                assert visible & all_courses == set(), table


async def test_private_course_is_visible_only_to_its_org(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    async with _as(tenant_session, cw.c_admin, cw.c) as s:
        assert cw.q.id in await _visible_courses(s)
    async with _as(tenant_session, cw.p_author, cw.p) as s:
        assert cw.q.id not in await _visible_courses(s)
    async with _as(tenant_session, cw.cse, cw.c) as s:  # C's students: not assigned to a batch
        assert cw.q.id not in await _visible_courses(s)


# --- batch-level visibility


async def test_batch_assignment_limits_visibility_to_that_batch(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    async with _as(tenant_session, cw.cse, cw.c) as s:
        cse_courses = await _visible_courses(s)
        cse_versions = await _ids(s, "SELECT id FROM course_versions")
    async with _as(tenant_session, cw.ece, cw.c) as s:
        ece_courses = await _visible_courses(s)

    assert cw.k.id in cse_courses  # K narrowed to CSE
    assert cw.k_version.id in cse_versions
    assert cw.k.id not in ece_courses
    assert cw.k2.id in ece_courses  # K2 assigned directly to ECE
    assert cw.k2.id not in cse_courses


async def test_org_grant_alone_shows_course_to_staff_not_students(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    for staff in (cw.c_admin, cw.c_instructor):
        async with _as(tenant_session, staff, cw.c) as s:
            assert cw.k4.id in await _visible_courses(s)
    for student in (cw.cse, cw.ece):
        async with _as(tenant_session, student, cw.c) as s:
            assert cw.k4.id not in await _visible_courses(s)


async def test_students_cannot_see_unassigned_courses_even_in_owner_org(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    async with _as(tenant_session, cw.p_student, cw.p) as s:
        visible = await _visible_courses(s)
        drafts = await _ids(s, "SELECT id FROM lessons")
        assignments = await _ids(s, "SELECT id FROM course_assignments")
    assert cw.k3.id in visible  # assigned to the student's batch
    assert visible & {cw.k.id, cw.k2.id, cw.k4.id} == set()
    assert drafts == set()
    assert assignments == set()  # students never see assignment rows


# --- narrowing, never widening


async def _insert_assignment(s: AsyncSession, **cols: Any) -> None:
    await s.execute(
        text(
            "INSERT INTO course_assignments (id, course_id, owner_organization_id, "
            "organization_id, batch_id, assigned_by_org_id, parent_assignment_id) VALUES "
            "(:id, :course_id, :owner, :org, :batch, :by, :parent)"
        ),
        {"id": new_id(), "batch": None, "parent": None, **cols},
    )


async def _cannot_insert(s: AsyncSession, **cols: Any) -> None:
    with pytest.raises(DBAPIError):
        async with s.begin_nested():
            await _insert_assignment(s, **cols)


async def test_org_admin_can_narrow_a_grant_to_own_batches(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    async with _as(tenant_session, cw.c_admin, cw.c) as s:
        await _insert_assignment(
            s, course_id=cw.k.id, owner=cw.p.id, org=cw.c.id, batch=cw.ece_batch.id,
            by=cw.c.id, parent=cw.grant_c.id,
        )  # fmt: skip


async def test_org_admin_cannot_widen(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    base = {"course_id": cw.k.id, "owner": cw.p.id, "org": cw.c.id}
    async with _as(tenant_session, cw.c_admin, cw.c) as s:
        # a new org grant for itself (only the publisher grants)
        await _cannot_insert(s, **base, by=cw.c.id)
        # posing as the publisher
        await _cannot_insert(s, **base, batch=cw.ece_batch.id, by=cw.p.id)
        # a batch row for a course the org was never granted (K2 only went to ECE directly)
        await _cannot_insert(
            s, course_id=cw.k2.id, owner=cw.p.id, org=cw.c.id, batch=cw.cse_batch.id,
            by=cw.c.id, parent=cw.grant_c.id,
        )  # fmt: skip
        # another org's batch
        await _cannot_insert(s, **base, batch=cw.o_batch.id, by=cw.c.id, parent=cw.grant_c.id)
        # assigning to a different org
        await _cannot_insert(
            s, course_id=cw.k.id, owner=cw.p.id, org=cw.o.id, batch=cw.o_batch.id,
            by=cw.o.id, parent=cw.grant_c.id,
        )  # fmt: skip


async def test_only_org_admins_distribute(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    # Instructors of the assigned org (and org admins of other orgs) can't narrow grants.
    narrow = {
        "course_id": cw.k.id, "owner": cw.p.id, "org": cw.c.id, "batch": cw.ece_batch.id,
        "by": cw.c.id, "parent": cw.grant_c.id,
    }  # fmt: skip
    async with _as(tenant_session, cw.c_instructor, cw.c) as s:
        await _cannot_insert(s, **narrow)
    async with _as(tenant_session, cw.o_admin, cw.o) as s:
        await _cannot_insert(s, **narrow)


async def test_removal_rights(cw: ContentWorld, tenant_session: TenantSessionFactory) -> None:
    delete = "DELETE FROM course_assignments WHERE id = :a"
    async with _as(tenant_session, cw.c_admin, cw.c) as s:
        assert await _rowcount(s, delete, a=cw.grant_c.id) == 0  # publisher-made grant
        assert await _rowcount(s, delete, a=cw.k2_ece.id) == 0  # publisher-made batch row
        assert await _rowcount(s, delete, a=cw.narrow_cse.id) == 1  # its own narrowing
    async with _as(tenant_session, cw.p_author, cw.p) as s:
        assert await _rowcount(s, delete, a=cw.k2_ece.id) == 1
        assert await _rowcount(s, delete, a=cw.grant_c.id) == 1
        # Removing the grant removes the narrowing built on it.
        left = await _ids(s, "SELECT id FROM course_assignments WHERE id = :a", a=cw.narrow_cse.id)
        assert left == set()


async def test_only_publishers_assign_to_other_orgs(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    async with _as(tenant_session, cw.c_admin, cw.c) as s:
        # C isn't a content publisher: its private course can't go to O...
        await _cannot_insert(s, course_id=cw.q.id, owner=cw.c.id, org=cw.o.id, by=cw.c.id)
        # ...but can go to C's own batches.
        await _insert_assignment(
            s, course_id=cw.q.id, owner=cw.c.id, org=cw.c.id, batch=cw.cse_batch.id, by=cw.c.id
        )
    async with _as(tenant_session, cw.p_author, cw.p) as s:
        await _insert_assignment(s, course_id=cw.k.id, owner=cw.p.id, org=cw.o.id, by=cw.p.id)


# --- immutability & others


async def test_published_versions_are_immutable(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    async with _as(tenant_session, cw.p_author, cw.p) as s:
        for sql in (
            "UPDATE course_versions SET title = 'x'",
            "DELETE FROM course_versions",
            "UPDATE course_version_lessons SET position = 9",
            "UPDATE course_assignments SET batch_id = NULL",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with s.begin_nested():
                    await s.execute(text(sql))


async def test_platform_admin_sees_everything(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    async with tenant_session(org=None, user=cw.platform_admin.id, platform_admin=True) as s:
        assert {cw.k.id, cw.q.id} <= await _visible_courses(s)
        assert cw.k_lesson.id in await _ids(s, "SELECT id FROM lessons")
