"""Two-level assignment through the API: publisher grants, org_admin narrowing (never widening),
removal rights, and the enrollment fan-out each change triggers."""

from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.identity.models import Organization, User
from tests.course_api import Campus, CourseApi, ok, published_for_cse


def _code(response: Response) -> str:
    return str(response.json()["error"]["code"])


async def test_grant_then_distribute_enrolls_only_that_batch(
    api: CourseApi, campus: Campus
) -> None:
    course = await api.build(campus.author, campus.p)
    ok(await api.publish(campus.author, campus.p, course.id), 201)

    grant = ok(await api.assign(campus.author, campus.p, course.id, to=campus.c), 201)
    assert [(a["kind"], a["organization_id"]) for a in grant] == [("org_grant", str(campus.c.id))]
    assert await api.run_jobs() == 0  # an org grant enrolls nobody
    assert await api.enrollments(campus.cse, campus.c) == []

    narrowed = ok(
        await api.assign(campus.c_admin, campus.c, course.id, batches=[campus.cse_batch]), 201
    )
    assert narrowed[0]["kind"] == "batch"
    assert narrowed[0]["parent_assignment_id"] == grant[0]["id"]
    assert narrowed[0]["assigned_by_org_id"] == str(campus.c.id)
    await api.run_jobs()

    cse = await api.enrollments(campus.cse, campus.c)
    assert [(e["course_id"], e["version"], e["progress_percent"]) for e in cse] == [
        (str(course.id), "1.0", 0)
    ]
    assert await api.enrollments(campus.ece, campus.c) == []


async def test_assigning_again_is_idempotent(api: CourseApi, campus: Campus) -> None:
    course = await published_for_cse(api, campus)
    again = ok(
        await api.assign(campus.c_admin, campus.c, course.id, batches=[campus.cse_batch]), 201
    )
    assert again == []


async def test_org_admin_cannot_widen(api: CourseApi, campus: Campus) -> None:
    course = await published_for_cse(api, campus)
    not_granted = await api.build(campus.author, campus.p)
    ok(await api.publish(campus.author, campus.p, not_granted.id), 201)
    # A publisher-made batch assignment alone isn't a grant the college can redistribute.
    ok(
        await api.assign(
            campus.author, campus.p, not_granted.id, to=campus.c, batches=[campus.ece_batch]
        ),
        201,
    )

    to_other_org = await api.assign(
        campus.c_admin, campus.c, course.id, to=campus.o, batches=[campus.o_batch]
    )
    org_grant = await api.assign(campus.c_admin, campus.c, course.id)
    ungranted = await api.assign(
        campus.c_admin, campus.c, not_granted.id, batches=[campus.cse_batch]
    )
    foreign_batch = await api.assign(campus.c_admin, campus.c, course.id, batches=[campus.o_batch])

    assert (to_other_org.status_code, _code(to_other_org)) == (403, "cannot_widen_assignment")
    assert (org_grant.status_code, _code(org_grant)) == (422, "batch_required")
    assert (ungranted.status_code, _code(ungranted)) == (403, "cannot_widen_assignment")
    assert (foreign_batch.status_code, _code(foreign_batch)) == (422, "invalid_batch")


async def test_only_org_admins_distribute(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p)
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    ok(await api.assign(campus.author, campus.p, course.id, to=campus.c), 201)

    by_instructor = await api.assign(
        campus.c_instructor, campus.c, course.id, batches=[campus.cse_batch]
    )
    listed = ok(
        await api.request("GET", f"/courses/{course.id}/assignments", campus.c_instructor, campus.c)
    )

    assert by_instructor.status_code == 403
    assert len(listed["items"]) == 1  # instructors can read the course and its assignments


async def test_unassigned_org_and_students_cannot_assign(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p)
    ok(await api.publish(campus.author, campus.p, course.id), 201)

    other = await api.assign(campus.o_admin, campus.o, course.id, batches=[campus.o_batch])
    student = await api.assign(campus.p_student, campus.p, course.id, batches=[campus.p_batch])

    assert other.status_code == 404
    assert student.status_code == 403


async def test_non_publisher_cannot_assign_to_other_orgs(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.c_instructor, campus.c, [("notes",)])
    ok(await api.publish(campus.c_instructor, campus.c, course.id), 201)

    to_other = await api.assign(campus.c_instructor, campus.c, course.id, to=campus.o)
    own_batch = await api.assign(
        campus.c_instructor, campus.c, course.id, batches=[campus.ece_batch]
    )
    own_org_grant = await api.assign(campus.c_instructor, campus.c, course.id)

    assert (to_other.status_code, _code(to_other)) == (403, "content_publisher_required")
    assert own_batch.status_code == 201
    assert (own_org_grant.status_code, _code(own_org_grant)) == (422, "batch_required")


async def test_owner_org_students_need_a_batch_assignment(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes",)])
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    await api.run_jobs()
    assert await api.enrollments(campus.p_student, campus.p) == []

    ok(await api.assign(campus.author, campus.p, course.id, batches=[campus.p_batch]), 201)
    await api.run_jobs()
    assert len(await api.enrollments(campus.p_student, campus.p)) == 1


async def test_unpublished_course_cannot_be_assigned(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p)
    response = await api.assign(campus.author, campus.p, course.id, to=campus.c)
    assert (response.status_code, _code(response)) == (409, "course_not_published")


async def test_removal_rights(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p)
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    grant = ok(await api.assign(campus.author, campus.p, course.id, to=campus.c), 201)[0]
    publisher_batch = ok(
        await api.assign(
            campus.author, campus.p, course.id, to=campus.c, batches=[campus.ece_batch]
        ),
        201,
    )[0]
    narrowed = ok(
        await api.assign(campus.c_admin, campus.c, course.id, batches=[campus.cse_batch]), 201
    )[0]

    async def delete(assignment: dict[str, str], user: User, org: Organization) -> int:
        path = f"/course-assignments/{assignment['id']}"
        return (await api.request("DELETE", path, user, org)).status_code

    # The college can't remove what the publisher made...
    assert await delete(publisher_batch, campus.c_admin, campus.c) == 403
    assert await delete(grant, campus.c_admin, campus.c) == 403
    # ...its instructors can't remove anything, and the publisher can't remove the college's row.
    assert await delete(narrowed, campus.c_instructor, campus.c) == 403
    assert await delete(narrowed, campus.author, campus.p) == 403
    # Each side removes its own rows.
    assert await delete(narrowed, campus.c_admin, campus.c) == 204
    assert await delete(publisher_batch, campus.author, campus.p) == 204


async def test_revoking_removes_access_and_reassigning_restores_progress(
    api: CourseApi, campus: Campus, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    course = await published_for_cse(api, campus, modules=[("notes", "notes")])
    enrollment = (await api.enrollments(campus.cse, campus.c))[0]
    ok(
        await api.request(
            "POST", f"/enrollments/{enrollment['id']}/lessons/{course.lesson_ids[0]}/complete",
            campus.cse, campus.c,
        )
    )  # fmt: skip
    rows = ok(
        await api.request("GET", f"/courses/{course.id}/assignments", campus.author, campus.p)
    )
    grant = next(a for a in rows["items"] if a["kind"] == "org_grant")

    # Removing the grant cascades to the college's batch row and revokes the enrollment.
    ok(
        await api.request("DELETE", f"/course-assignments/{grant['id']}", campus.author, campus.p),
        204,
    )
    await api.run_jobs()
    assert await api.enrollments(campus.cse, campus.c) == []
    gone = await api.request("GET", f"/enrollments/{enrollment['id']}", campus.cse, campus.c)
    assert gone.status_code == 404
    async with owner_sessionmaker() as s:
        remaining = await s.scalar(
            text("SELECT count(*) FROM course_assignments WHERE course_id = :c"), {"c": course.id}
        )
    assert remaining == 0

    # Re-assigning restores the enrollment with its progress.
    ok(
        await api.assign(
            campus.author, campus.p, course.id, to=campus.c, batches=[campus.cse_batch]
        ),
        201,
    )
    await api.run_jobs()
    restored = await api.enrollments(campus.cse, campus.c)
    assert [(e["id"], e["progress_percent"]) for e in restored] == [(enrollment["id"], 50)]


async def test_assignment_changes_are_audited(
    api: CourseApi, campus: Campus, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    course = await published_for_cse(api, campus)
    async with owner_sessionmaker() as s:
        rows = await s.execute(
            text(
                "SELECT action, organization_id FROM audit_log "
                "WHERE target_id = :c AND action LIKE 'course.assigned' ORDER BY id"
            ),
            {"c": str(course.id)},
        )
        assert [(a, o) for a, o in rows] == [
            ("course.assigned", campus.p.id),
            ("course.assigned", campus.c.id),
        ]
