"""Enrollments/progress (the student's org), media (the owner org's editors) and the public
catalog under RLS."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from tests.content_world import ContentWorld
from tests.factories import Factory
from tests.fixtures import TenantSessionFactory


async def _ids(s: AsyncSession, sql: str, **params: Any) -> set[Any]:
    return set(await s.scalars(text(sql), params))


async def test_enrollment_visibility(
    cw: ContentWorld, factory: Factory, tenant_session: TenantSessionFactory
) -> None:
    mine = await factory.enrollment(cw.k, cw.cse, cw.c)
    theirs = await factory.enrollment(cw.k2, cw.ece, cw.c)

    async with tenant_session(org=cw.c.id, user=cw.cse.id) as s:
        student_view = await _ids(s, "SELECT id FROM enrollments")
    async with tenant_session(org=cw.c.id, user=cw.c_instructor.id) as s:
        staff_view = await _ids(s, "SELECT id FROM enrollments")
    async with tenant_session(org=cw.o.id, user=cw.o_admin.id) as s:
        other_org_view = await _ids(s, "SELECT id FROM enrollments")
    async with tenant_session(org=cw.p.id, user=cw.p_author.id) as s:
        # The course owner doesn't see another org's enrollments (analytics come later, via events).
        owner_view = await _ids(s, "SELECT id FROM enrollments")

    assert mine.id in student_view
    assert theirs.id not in student_view
    assert {mine.id, theirs.id} <= staff_view
    assert other_org_view & {mine.id, theirs.id} == set()
    assert owner_view & {mine.id, theirs.id} == set()


async def test_students_write_only_their_own_progress(
    cw: ContentWorld, factory: Factory, tenant_session: TenantSessionFactory
) -> None:
    mine = await factory.enrollment(cw.k3, cw.cse, cw.c)
    theirs = await factory.enrollment(cw.k3, cw.ece, cw.c)
    progress = (
        "INSERT INTO lesson_progress (enrollment_id, lesson_id, organization_id, user_id, status) "
        "VALUES (:e, :l, :o, :u, 'in_progress')"
    )
    async with tenant_session(org=cw.c.id, user=cw.cse.id) as s:
        await s.execute(text(progress), {"e": mine.id, "l": new_id(), "o": cw.c.id, "u": cw.cse.id})
        own = await s.execute(
            text("UPDATE enrollments SET progress_percent = 50 WHERE id = :e"), {"e": mine.id}
        )
        other = await s.execute(
            text("UPDATE enrollments SET progress_percent = 100 WHERE id = :e"), {"e": theirs.id}
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            async with s.begin_nested():
                await s.execute(
                    text(progress), {"e": theirs.id, "l": new_id(), "o": cw.c.id, "u": cw.ece.id}
                )
        with pytest.raises(DBAPIError, match="row-level security"):
            async with s.begin_nested():
                await s.execute(
                    text(
                        "INSERT INTO enrollments (id, organization_id, user_id, course_id, "
                        "major_version) VALUES (:id, :o, :u, :c, 1)"
                    ),
                    {"id": new_id(), "o": cw.c.id, "u": cw.ece.id, "c": cw.k.id},
                )
        deleted = await s.execute(text("DELETE FROM enrollments WHERE id = :e"), {"e": mine.id})

    assert own.rowcount == 1  # type: ignore[attr-defined]
    assert other.rowcount == 0  # type: ignore[attr-defined]
    assert deleted.rowcount == 0  # type: ignore[attr-defined]


async def test_media_belongs_to_the_owner_orgs_editors(
    cw: ContentWorld, factory: Factory, tenant_session: TenantSessionFactory
) -> None:
    video = await factory.video(cw.p)
    async with tenant_session(org=cw.p.id, user=cw.p_author.id) as s:
        assert video.id in await _ids(s, "SELECT id FROM video_assets")
    for user, org in ((cw.p_student, cw.p), (cw.c_admin, cw.c), (cw.cse, cw.c)):
        async with tenant_session(org=org.id, user=user.id) as s:
            assert video.id not in await _ids(s, "SELECT id FROM video_assets")
            updated = await s.execute(
                text("UPDATE video_assets SET title = 'x' WHERE id = :v"), {"v": video.id}
            )
            assert updated.rowcount == 0  # type: ignore[attr-defined]


async def test_catalog_is_public_but_written_by_owner_editors(
    cw: ContentWorld, tenant_session: TenantSessionFactory
) -> None:
    entry = (
        "INSERT INTO catalog_entries (course_id, organization_id, version_id, slug, title, "
        "published_at) VALUES (:c, :o, :v, :slug, 'K', now())"
    )
    params = {"c": cw.k.id, "o": cw.p.id, "v": cw.k_version.id, "slug": f"k-{new_id().hex[:8]}"}
    async with tenant_session(org=cw.c.id, user=cw.c_admin.id) as s:
        with pytest.raises(DBAPIError, match="row-level security"):
            async with s.begin_nested():
                await s.execute(text(entry), params)
    async with tenant_session(org=cw.p.id, user=cw.p_author.id) as s:
        await s.execute(text(entry), params)
        # Visible to anyone, even without an organization or user.
        await s.execute(text("SELECT set_config('app.current_org', '', true)"))
        await s.execute(text("SELECT set_config('app.current_user', '', true)"))
        assert cw.k.id in await _ids(s, "SELECT course_id FROM catalog_entries")
