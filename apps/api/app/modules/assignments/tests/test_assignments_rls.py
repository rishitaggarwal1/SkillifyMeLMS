"""Row-Level Security for assignments (migration 0011), checked with raw SQL as the app role:

- students see only their own submissions and grades
- only the student's org grades; the course owner org and unrelated orgs see nothing
- graders may write progress only for a graded assignment lesson, and only progress columns
"""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.assignments.tests.test_assignments_api import (
    assignment_course,
    grade,
    submit_text,
)
from tests.course_api import Campus, CourseApi, ok
from tests.fixtures import TenantSessionFactory


async def _world(api: CourseApi, campus: Campus) -> dict[str, Any]:
    """CSE and a second CSE student each submit; the CSE student's work is graded."""
    second = await api.factory.member(campus.c, "student")
    await api.factory.add_to_batch(campus.cse_batch, second)
    course, eid, homework = await assignment_course(api, campus)
    graded = ok(await submit_text(api, campus, eid, homework, "mine", 0))["id"]
    ok(await grade(api, campus.c_instructor, campus.c, graded, 1))
    other = await api.enrollment_for(second, campus.c, course.id)
    assert other
    pending = ok(
        await api.request(
            "PUT",
            f"/enrollments/{other['id']}/lessons/{homework}/submission",
            second,
            campus.c,
            json={"submission": {"kind": "text", "text": "theirs"}},
            headers={"If-Match": "0"},
        )
    )["id"]
    return {
        "course": course,
        "homework": homework,
        "eid": UUID(eid),
        "other_eid": UUID(other["id"]),
        "graded": UUID(graded),
        "pending": UUID(pending),
        "second": second,
    }


_SELECT_IDS = {
    "assignment_submissions": text("SELECT id FROM assignment_submissions"),
    "assignment_grades": text("SELECT id FROM assignment_grades"),
}


async def _ids(s: Any, table: str) -> set[UUID]:
    return set(await s.scalars(_SELECT_IDS[table]))


async def test_who_sees_submissions_and_grades(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    w = await _world(api, campus)
    mine = {w["graded"]}
    async with tenant_session(org=campus.c.id, user=campus.cse.id) as s:
        assert await _ids(s, "assignment_submissions") == mine
        assert len(await _ids(s, "assignment_grades")) == 1
    async with tenant_session(org=campus.c.id, user=w["second"].id) as s:
        assert await _ids(s, "assignment_submissions") == {w["pending"]}
        assert await _ids(s, "assignment_grades") == set()  # not graded yet
    async with tenant_session(org=campus.c.id, user=campus.c_instructor.id) as s:
        assert {w["graded"], w["pending"]} <= await _ids(s, "assignment_submissions")
    # The course owner org and an unrelated org see none of the college's work.
    for org, user in (
        (campus.p, campus.author),
        (campus.p, campus.p_admin),
        (campus.o, campus.o_admin),
    ):
        async with tenant_session(org=org.id, user=user.id) as s:
            assert not ({w["graded"], w["pending"]} & await _ids(s, "assignment_submissions"))
            assert await _ids(s, "assignment_grades") == set()
    # A student acting in another org they don't belong to sees nothing either.
    async with tenant_session(org=campus.o.id, user=campus.cse.id) as s:
        assert await _ids(s, "assignment_submissions") == set()


async def test_students_cannot_write_others_or_graded_work(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    w = await _world(api, campus)
    async with tenant_session(org=campus.c.id, user=campus.cse.id) as s:
        # Changing a graded submission: invisible to UPDATE (status must be 'submitted').
        changed = await s.execute(
            text("UPDATE assignment_submissions SET text_body = 'x' WHERE id = :id"),
            {"id": w["graded"]},
        )
        assert changed.rowcount == 0  # type: ignore[attr-defined]
        # Someone else's pending submission: not visible, so nothing changes.
        changed = await s.execute(
            text("UPDATE assignment_submissions SET text_body = 'x' WHERE id = :id"),
            {"id": w["pending"]},
        )
        assert changed.rowcount == 0  # type: ignore[attr-defined]
    async with tenant_session(org=campus.c.id, user=campus.cse.id) as s:
        # Submitting into another student's enrollment is refused by the INSERT check.
        with pytest.raises(DBAPIError):
            await s.execute(
                text(
                    "INSERT INTO assignment_submissions (id, organization_id, assignment_id, "
                    "course_id, lesson_id, version_id, enrollment_id, user_id, kind, text_body) "
                    "SELECT gen_random_uuid(), organization_id, assignment_id, course_id, "
                    "lesson_id, version_id, :other, :me, 'text', 'x' "
                    "FROM assignment_submissions WHERE id = :graded"
                ),
                {"other": w["other_eid"], "me": campus.cse.id, "graded": w["graded"]},
            )
    async with tenant_session(org=campus.c.id, user=campus.cse.id) as s:
        # Students never write grades.
        with pytest.raises(DBAPIError):
            await s.execute(
                text(
                    "INSERT INTO assignment_grades (id, organization_id, submission_id, user_id, "
                    "score, max_marks) VALUES (gen_random_uuid(), :org, :sub, :me, 10, 10)"
                ),
                {"org": campus.c.id, "sub": w["pending"], "me": campus.cse.id},
            )


async def test_graders_write_progress_only_after_grading(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    w = await _world(api, campus)
    notes = w["course"].lesson_ids[0]
    write = (
        "INSERT INTO lesson_progress (enrollment_id, lesson_id, organization_id, user_id, status) "
        "VALUES (:e, :l, :org, :u, 'completed') "
        "ON CONFLICT (enrollment_id, lesson_id) DO UPDATE SET status = 'completed'"
    )
    params = {"org": campus.c.id}
    async with tenant_session(org=campus.c.id, user=campus.c_instructor.id) as s:
        # The graded assignment lesson: allowed (this is what grading does).
        await s.execute(
            text(write), {**params, "e": w["eid"], "l": w["homework"], "u": campus.cse.id}
        )
    refused = (
        (w["eid"], notes, campus.cse.id),  # another lesson of a graded enrollment
        (w["other_eid"], w["homework"], w["second"].id),  # an ungraded submission
    )
    for enrollment, lesson, student in refused:
        async with tenant_session(org=campus.c.id, user=campus.c_instructor.id) as s:
            with pytest.raises(DBAPIError):
                await s.execute(text(write), {**params, "e": enrollment, "l": lesson, "u": student})
    # Graders may update a graded enrollment's progress, never anything else on it.
    async with tenant_session(org=campus.c.id, user=campus.c_instructor.id) as s:
        updated = await s.execute(
            text("UPDATE enrollments SET progress_percent = 100 WHERE id = :id"), {"id": w["eid"]}
        )
        assert updated.rowcount == 1  # type: ignore[attr-defined]
    async with tenant_session(org=campus.c.id, user=campus.c_instructor.id) as s:
        with pytest.raises(DBAPIError, match="graders may only update enrollment progress"):
            await s.execute(
                text("UPDATE enrollments SET status = 'revoked' WHERE id = :id"), {"id": w["eid"]}
            )
    async with tenant_session(org=campus.c.id, user=campus.c_instructor.id) as s:
        ungraded = await s.execute(
            text("UPDATE enrollments SET progress_percent = 100 WHERE id = :id"),
            {"id": w["other_eid"]},
        )
        assert ungraded.rowcount == 0  # type: ignore[attr-defined]


async def test_submission_files_are_private(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    _, eid, homework = await assignment_course(api, campus)
    created = ok(
        await api.request(
            "POST",
            f"/enrollments/{eid}/lessons/{homework}/submission-upload",
            campus.cse,
            campus.c,
            json={"file_name": "a.pdf", "content_type": "application/pdf"},
        ),
        201,
    )
    file_id = UUID(created["file"]["id"])
    visible = "SELECT count(*) FROM files WHERE id = :id"
    async with tenant_session(org=campus.c.id, user=campus.cse.id) as s:
        assert await s.scalar(text(visible), {"id": file_id}) == 1
    async with tenant_session(org=campus.c.id, user=campus.c_instructor.id) as s:
        assert await s.scalar(text(visible), {"id": file_id}) == 1
    for org, user in (
        (campus.c, campus.ece),
        (campus.p, campus.author),
        (campus.o, campus.o_admin),
    ):
        async with tenant_session(org=org.id, user=user.id) as s:
            assert await s.scalar(text(visible), {"id": file_id}) == 0
    async with tenant_session(org=campus.c.id, user=campus.ece.id) as s:
        # Students may only upload submissions, as themselves.
        with pytest.raises(DBAPIError):
            await s.execute(
                text(
                    "INSERT INTO files (id, organization_id, kind, storage_key, file_name, "
                    "content_type, created_by) VALUES (gen_random_uuid(), :org, 'pdf', :key, "
                    "'x.pdf', 'application/pdf', :me)"
                ),
                {"org": campus.c.id, "key": f"x/{file_id}", "me": campus.ece.id},
            )
