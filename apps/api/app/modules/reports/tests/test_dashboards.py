"""Approved role summaries: real state, tenant/batch isolation and cursor ordering."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from app.modules.enrollments.tests.test_caching import statements
from tests.course_api import Campus, CourseApi, ok, published_for_cse


async def _submit(api: CourseApi, campus: Campus, course_id: UUID, lesson: UUID) -> str:
    eid = (await api.enrollment_for(campus.cse, campus.c, course_id) or {})["id"]
    return str(
        ok(
            await api.request(
                "PUT",
                f"/enrollments/{eid}/lessons/{lesson}/submission",
                campus.cse,
                campus.c,
                json={"submission": {"kind": "text", "text": "PRIVATE WORK"}},
                headers={"If-Match": "0"},
            )
        )["id"]
    )


async def test_cross_course_queue_ungraded_first_and_tenant_isolation(
    api: CourseApi, campus: Campus
) -> None:
    ids = []
    for _ in range(3):
        course = await published_for_cse(api, campus, modules=(("assignment",),))
        ids.append(await _submit(api, campus, course.id, course.lesson_ids[0]))
    ok(
        await api.request(
            "PUT",
            f"/assignment-submissions/{ids[0]}/grade",
            campus.c_instructor,
            campus.c,
            json={"score": "8"},
            headers={"If-Match": "1"},
        )
    )
    seen: list[str] = []
    cursor = None
    while True:
        page = ok(
            await api.request(
                "GET",
                "/assignment-submissions",
                campus.c_instructor,
                campus.c,
                params={"limit": 1, **({"cursor": cursor} if cursor else {})},
            )
        )
        seen.extend(r["id"] for r in page["items"])
        assert "PRIVATE WORK" not in str(page)
        if not (cursor := page["next_cursor"]):
            break
    assert seen == [ids[1], ids[2], ids[0]]
    summary = ok(await api.request("GET", "/dashboards/teach", campus.c_instructor, campus.c))
    assert summary["ungraded_count"] == 2
    assert summary["oldest_ungraded_at"]
    for user, org in ((campus.author, campus.p), (campus.o_admin, campus.o)):
        assert ok(await api.request("GET", "/assignment-submissions", user, org))["items"] == []
        assert ok(await api.request("GET", "/dashboards/teach", user, org))["ungraded_count"] == 0


async def test_due_soon_pinned_version_cursor_and_unsubmitted_only(
    api: CourseApi, campus: Campus
) -> None:
    course = await api.build(
        campus.author, campus.p, modules=(("assignment", "assignment", "assignment"),)
    )
    now = datetime.now(UTC)
    for index, lesson in enumerate(course.lesson_ids):
        ok(
            await api.define_assignment(
                campus.author,
                campus.p,
                course.id,
                lesson,
                due_at=(now + timedelta(days=2 if index < 2 else 8)).isoformat(),
            )
        )
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    ok(await api.assign(campus.author, campus.p, course.id, to=campus.c), 201)
    ok(await api.assign(campus.c_admin, campus.c, course.id, batches=[campus.cse_batch]), 201)
    await api.run_jobs()
    first = ok(
        await api.request("GET", "/dashboards/learn/due", campus.cse, campus.c, params={"limit": 1})
    )
    second = ok(
        await api.request(
            "GET",
            "/dashboards/learn/due",
            campus.cse,
            campus.c,
            params={"limit": 1, "cursor": first["next_cursor"]},
        )
    )
    assert [r["lesson_id"] for r in first["items"] + second["items"]] == [
        str(lesson) for lesson in sorted(course.lesson_ids[:2])
    ]
    assert second["next_cursor"] is None
    assert (
        ok(await api.request("GET", "/dashboards/learn/due", campus.ece, campus.c))["items"] == []
    )
    await _submit(api, campus, course.id, course.lesson_ids[0])
    assert [
        r["lesson_id"]
        for r in ok(await api.request("GET", "/dashboards/learn/due", campus.cse, campus.c))[
            "items"
        ]
    ] == [str(course.lesson_ids[1])]
    # A new major changes the deadline, but this enrollment still sees major 1.
    ok(
        await api.define_assignment(
            campus.author, campus.p, course.id, course.lesson_ids[1], due_at=None, max_marks=20
        )
    )
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    assert (
        len(ok(await api.request("GET", "/dashboards/learn/due", campus.cse, campus.c))["items"])
        == 1
    )
    assignments = ok(
        await api.request("GET", f"/courses/{course.id}/assignments", campus.c_admin, campus.c)
    )["items"]
    batch = next(a for a in assignments if a["batch_id"] == str(campus.cse_batch.id))
    ok(
        await api.request("DELETE", f"/course-assignments/{batch['id']}", campus.c_admin, campus.c),
        204,
    )
    # No worker fan-out needed: live access, rather than stale enrollment status.
    assert (
        ok(await api.request("GET", "/dashboards/learn/due", campus.cse, campus.c))["items"] == []
    )


async def test_recent_grades_latest_revision_only_and_no_other_student(
    api: CourseApi, campus: Campus
) -> None:
    course = await published_for_cse(api, campus, modules=(("assignment", "assignment"),))
    ids = [await _submit(api, campus, course.id, lesson) for lesson in course.lesson_ids]
    for sid in ids:
        for rev, score in ((1, "6"), (2, "8")):
            ok(
                await api.request(
                    "PUT",
                    f"/assignment-submissions/{sid}/grade",
                    campus.c_instructor,
                    campus.c,
                    json={"score": score, "feedback": "PRIVATE FEEDBACK"},
                    headers={"If-Match": str(rev)},
                )
            )
    rows = []
    cursor = None
    while True:
        page = ok(
            await api.request(
                "GET",
                "/dashboards/learn/results",
                campus.cse,
                campus.c,
                params={"limit": 1, **({"cursor": cursor} if cursor else {})},
            )
        )
        rows.extend(page["items"])
        if not (cursor := page["next_cursor"]):
            break
    assert len(rows) == 2
    assert {r["score"] for r in rows} == {"8.00"}
    assert "PRIVATE" not in str(rows)
    assert (
        ok(await api.request("GET", "/dashboards/learn/results", campus.ece, campus.c))["items"]
        == []
    )


async def test_admin_overview_real_checklist_and_unassigned_grants(
    api: CourseApi, campus: Campus
) -> None:
    org = await api.factory.org()
    admin = await api.factory.member(org, "org_admin")

    def path() -> str:
        return "/dashboards/admin"

    state = ok(await api.request("GET", path(), admin, org))
    assert state == {
        "has_batch": False,
        "has_students": False,
        "has_assignment": False,
        "pending_invitations": 0,
        "running_imports": 0,
        "failed_imports": 0,
    }
    batch = await api.factory.batch(org)
    await api.factory.invitation(org)
    state = ok(await api.request("GET", path(), admin, org))
    assert state["has_batch"]
    assert state["has_students"]
    assert state["pending_invitations"] == 1
    course = await api.build(campus.author, campus.p, modules=(("notes",),))
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    ok(await api.assign(campus.author, campus.p, course.id, to=org), 201)
    grants = ok(await api.request("GET", "/dashboards/admin/unassigned-courses", admin, org))
    assert [c["id"] for c in grants["items"]] == [str(course.id)]
    assert (
        ok(
            await api.request(
                "GET", "/dashboards/admin/unassigned-courses", campus.o_admin, campus.o
            )
        )["items"]
        == []
    )
    ok(await api.assign(admin, org, course.id, batches=[batch]), 201)
    assert ok(await api.request("GET", path(), admin, org))["has_assignment"]
    assert (
        ok(await api.request("GET", "/dashboards/admin/unassigned-courses", admin, org))["items"]
        == []
    )


async def test_batch_completion_and_inactivity_are_scoped_and_batched(
    api: CourseApi, campus: Campus
) -> None:
    course = await published_for_cse(api, campus, modules=(("notes", "assignment"),))
    eid = (await api.enrollment_for(campus.cse, campus.c, course.id) or {})["id"]
    ok(
        await api.request(
            "POST",
            f"/enrollments/{eid}/lessons/{course.lesson_ids[0]}/complete",
            campus.cse,
            campus.c,
        )
    )
    async with api.factory.sessionmaker() as s:
        await s.execute(
            text("UPDATE enrollments SET last_accessed_at=now()-interval '8 days' WHERE id=:id"),
            {"id": UUID(eid)},
        )
        await s.commit()
    ok(await api.request("GET", "/dashboards/admin/batches", campus.c_admin, campus.c))
    with statements(api) as queries:
        page = ok(await api.request("GET", "/dashboards/admin/batches", campus.c_admin, campus.c))
    by_id = {b["id"]: b for b in page["items"]}
    assert by_id[str(campus.cse_batch.id)]["completion_percent"] == 50
    assert by_id[str(campus.cse_batch.id)]["last_activity_at"]
    assert by_id[str(campus.ece_batch.id)]["completion_percent"] is None
    assert (
        ok(await api.request("GET", "/dashboards/teach", campus.c_instructor, campus.c))[
            "inactive_students"
        ]
        == 1
    )
    for i in range(6):
        await api.factory.batch(campus.c, name=f"Extra {i}")
    with statements(api) as larger:
        ok(await api.request("GET", "/dashboards/admin/batches", campus.c_admin, campus.c))
    assert len(larger) == len(queries)
    assert (
        ok(await api.request("GET", "/dashboards/teach", campus.author, campus.p))[
            "inactive_students"
        ]
        == 0
    )


@pytest.mark.parametrize(
    "path",
    [
        "/assignment-submissions",
        "/dashboards/admin/batches",
        "/dashboards/admin/unassigned-courses",
        "/dashboards/learn/due",
        "/dashboards/learn/results",
    ],
)
async def test_dashboard_lists_reject_invalid_cursor(
    api: CourseApi, campus: Campus, path: str
) -> None:
    user = campus.cse if "/learn/" in path else campus.c_admin
    response = await api.request("GET", path, user, campus.c, params={"cursor": "invalid"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_cursor"
