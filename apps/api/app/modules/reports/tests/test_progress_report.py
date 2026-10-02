"""Progress reports (stopgap until Phase 5): rows and cells, who may read them, paging by name,
a fixed number of queries per page (no N+1), CSV export, older versions, and batch summaries."""

import csv
import io
from typing import Any

import pytest

from app.modules.enrollments.tests.test_caching import statements
from app.modules.identity.models import User
from app.modules.reports import service as reports_service
from tests.course_api import BuiltCourse, Campus, CourseApi, ok, published_for_cse


async def _students(api: CourseApi, campus: Campus, *names: str) -> list[User]:
    """More CSE students, added before the course is assigned (fan-out enrolls them)."""
    users = []
    for name in names:
        user = await api.factory.member(
            campus.c, "student", user=await api.factory.user(full_name=name)
        )
        await api.factory.add_to_batch(campus.cse_batch, user)
        users.append(user)
    return users


def _progress(course: BuiltCourse, campus: Campus, **params: Any) -> tuple[str, dict[str, Any]]:
    return f"/courses/{course.id}/progress", {
        "params": {"batch_id": str(campus.cse_batch.id), **params}
    }


async def _get(api: CourseApi, campus: Campus, course: BuiltCourse, **params: Any) -> Any:
    path, kwargs = _progress(course, campus, **params)
    return ok(await api.request("GET", path, campus.c_instructor, campus.c, **kwargs))


async def test_rows_cells_and_assignment_status(api: CourseApi, campus: Campus) -> None:
    [meera] = await _students(api, campus, "Meera Iyer")
    course = await published_for_cse(api, campus, modules=(("notes", "assignment"),))
    notes, homework = course.lesson_ids
    eid = (await api.enrollment_for(campus.cse, campus.c, course.id) or {})["id"]
    ok(
        await api.request(
            "POST", f"/enrollments/{eid}/lessons/{notes}/complete", campus.cse, campus.c
        )
    )
    sid = ok(
        await api.request(
            "PUT",
            f"/enrollments/{eid}/lessons/{homework}/submission",
            campus.cse,
            campus.c,
            json={"submission": {"kind": "text", "text": "x"}},
            headers={"If-Match": "0"},
        )
    )["id"]
    ok(
        await api.request(
            "PUT",
            f"/assignment-submissions/{sid}/grade",
            campus.c_instructor,
            campus.c,
            json={"score": "7.5"},
            headers={"If-Match": "1"},
        )
    )

    page = await _get(api, campus, course)
    assert page["version"] == "1.0"
    assert [(c["id"], c["lesson_type"]) for c in page["lessons"]] == [
        (str(notes), "notes"),
        (str(homework), "assignment"),
    ]
    rows = {r["student"]["id"]: r for r in page["items"]}
    assert set(rows) == {str(campus.cse.id), str(meera.id)}
    done = rows[str(campus.cse.id)]
    assert (done["progress_percent"], done["version"], done["enrollment_status"]) == (
        100,
        "1.0",
        "active",
    )
    assert done["lessons"] == {str(notes): "completed", str(homework): "completed"}
    assert done["assignments"] == {
        str(homework): {"status": "graded", "score": "7.50", "max_marks": 10}
    }
    assert done["completed_at"]
    assert done["last_activity_at"]
    idle = rows[str(meera.id)]
    assert idle["progress_percent"] == 0
    assert idle["lessons"] == {str(notes): "not_started", str(homework): "not_started"}
    assert idle["assignments"] == {}


async def test_who_may_read_it(api: CourseApi, campus: Campus) -> None:
    course = await published_for_cse(api, campus, modules=(("notes",),))
    path = f"/courses/{course.id}/progress"
    cse = {"params": {"batch_id": str(campus.cse_batch.id)}}
    assert (await api.request("GET", path, campus.c_admin, campus.c, **cse)).status_code == 200
    # The ECE batch wasn't assigned the course; another org's batch isn't the org's.
    for batch in (campus.ece_batch, campus.o_batch, campus.p_batch):
        response = await api.request(
            "GET", path, campus.c_instructor, campus.c, params={"batch_id": str(batch.id)}
        )
        assert response.status_code == 404, batch.name
    # The publisher can't read the college's students; an unrelated org can't see the course.
    assert (await api.request("GET", path, campus.author, campus.p, **cse)).status_code == 404
    assert (await api.request("GET", path, campus.o_admin, campus.o, **cse)).status_code == 404
    assert (await api.request("GET", path, campus.cse, campus.c, **cse)).status_code == 403
    missing = await api.request("GET", path, campus.c_instructor, campus.c)
    assert missing.status_code == 422  # batch_id is required


async def test_pages_by_name(api: CourseApi, campus: Campus) -> None:
    await _students(api, campus, "zoya khan", "Aarav Shah", "Ishaan Rao")
    course = await published_for_cse(api, campus, modules=(("notes",),))
    seen: list[str] = []
    cursor = None
    while True:
        page = await _get(api, campus, course, limit=2, **({"cursor": cursor} if cursor else {}))
        seen += [r["student"]["full_name"] for r in page["items"]]
        if not (cursor := page["next_cursor"]):
            break
    named = [n for n in seen if not n.startswith("User ")]  # the campus's own CSE student
    assert named == ["Aarav Shah", "Ishaan Rao", "zoya khan"]  # case-insensitive by name
    assert len(seen) == len(set(seen)) == 4


async def test_queries_per_page_dont_grow_with_students(api: CourseApi, campus: Campus) -> None:
    async def count(course: BuiltCourse) -> int:
        await _get(api, campus, course)  # warm the version cache
        with statements(api) as seen:
            page = await _get(api, campus, course)
        assert page["items"]
        return len(seen)

    small = await published_for_cse(api, campus, modules=(("notes", "assignment"),))
    few = await count(small)
    await _students(api, campus, *(f"Student {i:02}" for i in range(12)))
    big = await published_for_cse(api, campus, modules=(("notes", "assignment"),))
    many = await count(big)
    assert many == few, f"{few} queries for 1 student, {many} for 13"


async def test_csv_export(api: CourseApi, campus: Campus) -> None:
    [risky] = await _students(api, campus, '=HYPERLINK("http://evil")')
    course = await published_for_cse(api, campus, modules=(("notes", "assignment"),))
    path, kwargs = _progress(course, campus)
    response = await api.request("GET", f"{path}.csv", campus.c_admin, campus.c, **kwargs)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].startswith('attachment; filename="progress-')
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][:5] == ["Name", "Email", "Enrollment", "Version", "Progress %"]
    assert rows[0][7:] == ["Module 1 / notes lesson", "Module 1 / assignment lesson"]
    by_email = {r[1]: r for r in rows[1:]}
    assert by_email[risky.email][0] == '\'=HYPERLINK("http://evil")'  # neutralized
    assert by_email[campus.cse.email][2:5] == ["active", "1.0", "0"]


async def test_older_versions_and_batch_summary(api: CourseApi, campus: Campus) -> None:
    course = await published_for_cse(api, campus, modules=(("notes",),))
    # A major 2 adds a lesson; the CSE enrollment stays on major 1 until the college opts in.
    module = course.module_ids[0]
    added = ok(
        await api.request(
            "POST",
            f"/courses/{course.id}/modules/{module}/lessons",
            campus.author,
            campus.p,
            json={
                "title": "Extra",
                "lesson_type": "notes",
                "content": {"doc": {"type": "doc", "content": []}},
            },
        ),
        201,
    )
    ok(await api.publish(campus.author, campus.p, course.id, "major"), 201)
    page = await _get(api, campus, course)
    assert page["version"] == "2.0"
    [row] = [r for r in page["items"] if r["student"]["id"] == str(campus.cse.id)]
    assert row["version"] == "1.0"
    assert row["lessons"][added["id"]] == "not_in_version"

    eid = (await api.enrollment_for(campus.cse, campus.c, course.id) or {})["id"]
    ok(
        await api.request(
            "POST",
            f"/enrollments/{eid}/lessons/{course.lesson_ids[0]}/complete",
            campus.cse,
            campus.c,
        )
    )
    summary = ok(
        await api.request(
            "GET", f"/batches/{campus.cse_batch.id}/courses", campus.c_admin, campus.c
        )
    )
    [item] = [i for i in summary["items"] if i["course_id"] == str(course.id)]
    assert (item["enrolled"], item["completed"], item["average_percent"]) == (1, 1, 100)
    assert item["version"] == "2.0"
    elsewhere = await api.request(
        "GET", f"/batches/{campus.o_batch.id}/courses", campus.c_admin, campus.c
    )
    assert elsewhere.status_code == 404
    ece = ok(
        await api.request(
            "GET", f"/batches/{campus.ece_batch.id}/courses", campus.c_admin, campus.c
        )
    )
    assert ece["items"] == []


async def test_csv_export_is_capped(
    api: CourseApi, campus: Campus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Above the cap (10,000 rows; 2 here) the export is refused before anything is built."""
    monkeypatch.setattr(reports_service, "CSV_MAX_ROWS", 2)
    await _students(api, campus, "Aarav", "Bhavna")  # 3 students with the campus's own
    course = await published_for_cse(api, campus, modules=(("notes",),))
    path, kwargs = _progress(course, campus)
    response = await api.request("GET", f"{path}.csv", campus.c_admin, campus.c, **kwargs)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "export_too_large"
    assert error["details"] == {"rows": 3, "max_rows": 2}
    # The paginated table still works for the same batch.
    assert len((await _get(api, campus, course, limit=100))["items"]) == 3
