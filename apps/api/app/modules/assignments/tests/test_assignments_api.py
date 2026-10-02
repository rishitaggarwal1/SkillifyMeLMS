"""Assignments: authoring and publishing, student submissions (text and real MinIO files),
grading with completion and progress, and the If-Match / 404 rules."""

from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import func, select

from app.db.outbox import OutboxEvent
from app.modules.identity.models import Organization, User
from tests.course_api import BuiltCourse, Campus, CourseApi, ok, published_for_cse

PDF = b"%PDF-1.7\n" + b"0" * 64


async def assignment_course(api: CourseApi, campus: Campus) -> tuple[BuiltCourse, str, UUID]:
    """Notes + assignment, published to CSE: (course, CSE enrollment id, assignment lesson)."""
    course = await published_for_cse(api, campus, modules=(("notes", "assignment"),))
    enrollment = await api.enrollment_for(campus.cse, campus.c, course.id)
    assert enrollment
    return course, enrollment["id"], course.lesson_ids[1]


def mine(eid: str, lesson: UUID, suffix: str = "assignment") -> str:
    return f"/enrollments/{eid}/lessons/{lesson}/{suffix}"


async def submit_text(
    api: CourseApi, campus: Campus, eid: str, lesson: UUID, text: str, revision: int
) -> httpx.Response:
    return await api.request(
        "PUT",
        mine(eid, lesson, "submission"),
        campus.cse,
        campus.c,
        json={"submission": {"kind": "text", "text": text}},
        headers={"If-Match": str(revision)},
    )


async def grade(
    api: CourseApi,
    user: User,
    org: Organization,
    submission_id: str,
    revision: int | None,
    **body: Any,
) -> httpx.Response:
    headers = {} if revision is None else {"If-Match": str(revision)}
    return await api.request(
        "PUT",
        f"/assignment-submissions/{submission_id}/grade",
        user,
        org,
        json={"score": "8", "feedback": "Good", **body},
        headers=headers,
    )


async def lesson_completed_events(api: CourseApi, lesson: UUID) -> int:
    async with api.factory.sessionmaker() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "lesson_completed",
                    OutboxEvent.payload["lesson_id"].astext == str(lesson),
                )
            )
            or 0
        )


# ---------------------------------------------------------------------------- authoring


async def test_definition_is_required_before_publishing(api: CourseApi, campus: Campus) -> None:
    course = ok(
        await api.request("POST", "/courses", campus.author, campus.p, json={"title": "Python"}),
        201,
    )
    module = ok(
        await api.request(
            "POST", f"/courses/{course['id']}/modules", campus.author, campus.p, json={"title": "M"}
        ),
        201,
    )
    lesson = ok(
        await api.request(
            "POST",
            f"/courses/{course['id']}/modules/{module['id']}/lessons",
            campus.author,
            campus.p,
            json={"title": "Homework", "lesson_type": "assignment"},
        ),
        201,
    )
    assert lesson["is_required"] is True  # assignments count toward progress now
    path = f"/courses/{course['id']}/lessons/{lesson['id']}/assignment"
    assert ok(await api.request("GET", path, campus.author, campus.p)) is None

    blocked = await api.publish(campus.author, campus.p, UUID(course["id"]))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"] == [
        {"code": "assignment_not_ready", "lesson_ids": [lesson["id"]]}
    ]

    # If-Match is the course revision: required (428) and checked (409).
    no_header = await api.client.put(
        f"/api/v1{path}",
        headers=api.h(campus.author, campus.p),
        json={"title": "T", "max_marks": 5, "submission_kinds": ["text"]},
    )
    assert no_header.status_code == 428
    stale = await api.request(
        "PUT",
        path,
        campus.author,
        campus.p,
        headers={"If-Match": "1"},
        json={"title": "T", "max_marks": 5, "submission_kinds": ["text"]},
    )
    assert stale.status_code == 409
    saved = ok(
        await api.define_assignment(campus.author, campus.p, UUID(course["id"]), UUID(lesson["id"]))
    )
    assert (saved["max_marks"], saved["submission_kinds"]) == (10, ["file", "text"])
    assert saved["course_revision"] > 1
    ok(await api.publish(campus.author, campus.p, UUID(course["id"])), 201)


async def test_definition_validation_and_access(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, modules=(("notes", "assignment"),))
    notes, homework = course.lesson_ids
    path = f"/courses/{course.id}/lessons/{homework}/assignment"

    image_doc = {
        "type": "doc",
        "content": [{"type": "image", "attrs": {"file_id": str(UUID(int=1))}}],
    }
    bad: list[tuple[dict[str, Any], str]] = [
        (
            {"instructions": {"type": "doc", "content": [{"type": "script"}]}},
            "invalid_instructions",
        ),
        ({"instructions": image_doc}, "instructions_images"),
    ]
    for fields, code in bad:
        response = await api.define_assignment(
            campus.author, campus.p, course.id, homework, **fields
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == code
    invalid: list[dict[str, Any]] = [
        {"max_marks": 0},
        {"submission_kinds": []},
        {"submission_kinds": ["text", "text"]},
        {"submission_kinds": ["zip"]},
        {"due_at": "2026-10-10T10:00:00"},
    ]
    for fields in invalid:
        assert (
            await api.define_assignment(campus.author, campus.p, course.id, homework, **fields)
        ).status_code == 422

    # Lesson edits can't set an assignment's content; only this endpoint does.
    patched = await api.request(
        "PATCH",
        f"/courses/{course.id}/lessons/{homework}",
        campus.author,
        campus.p,
        json={"content": {"assignment_id": str(UUID(int=2))}},
    )
    assert patched.status_code == 422
    assert patched.json()["error"]["code"] == "assignment_content_managed_separately"
    # Not an assignment lesson; another org; a student: 404 / 403 like other editor routes.
    notes_path = f"/courses/{course.id}/lessons/{notes}/assignment"
    assert (await api.request("GET", notes_path, campus.author, campus.p)).status_code == 404
    assert (await api.request("GET", path, campus.o_admin, campus.o)).status_code == 404
    assert (await api.request("GET", path, campus.c_instructor, campus.c)).status_code == 404


async def test_publish_snapshot_and_structural_rule(api: CourseApi, campus: Campus) -> None:
    course, eid, homework = await assignment_course(api, campus)
    seen = ok(await api.request("GET", mine(eid, homework), campus.cse, campus.c))
    assert seen["assignment"]["title"] == "FizzBuzz"
    assert seen["assignment"]["instructions_html"] == "<p>Solve.</p>"
    assert seen["assignment"]["max_marks"] == 10
    assert seen["submission"] is None

    # Title, instructions and due date are corrections: a minor reaches the student.
    ok(
        await api.define_assignment(
            campus.author,
            campus.p,
            course.id,
            homework,
            title="FizzBuzz v2",
            due_at="2026-11-01T18:30:00+00:00",
        )
    )
    ok(await api.publish(campus.author, campus.p, course.id, "minor"), 201)
    seen = ok(await api.request("GET", mine(eid, homework), campus.cse, campus.c))
    assert seen["assignment"]["title"] == "FizzBuzz v2"
    assert seen["assignment"]["due_at"].startswith("2026-11-01T18:30:00")

    # Marks and allowed kinds are structural: existing grades depend on them.
    for fields in ({"max_marks": 20}, {"submission_kinds": ["text"]}):
        ok(await api.define_assignment(campus.author, campus.p, course.id, homework, **fields))
        minor = await api.publish(campus.author, campus.p, course.id, "minor")
        assert minor.status_code == 409
        assert minor.json()["error"]["code"] == "minor_not_allowed"
        ok(
            await api.define_assignment(
                campus.author,
                campus.p,
                course.id,
                homework,
                title="FizzBuzz v2",
                due_at="2026-11-01T18:30:00+00:00",
            )
        )


# ---------------------------------------------------------------------------- students


async def test_text_submission_resubmit_and_if_match(api: CourseApi, campus: Campus) -> None:
    course, eid, homework = await assignment_course(api, campus)
    path = mine(eid, homework, "submission")

    no_header = await api.client.put(
        f"/api/v1{path}",
        headers=api.h(campus.cse, campus.c),
        json={"submission": {"kind": "text", "text": "print(1)"}},
    )
    assert no_header.status_code == 428
    first = ok(await submit_text(api, campus, eid, homework, "  print(1)  ", 0))
    assert (first["status"], first["revision"], first["text_body"]) == ("submitted", 1, "print(1)")
    stale = await submit_text(api, campus, eid, homework, "print(2)", 0)
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == {"current_revision": 1}
    second = ok(await submit_text(api, campus, eid, homework, "print(2)", 1))
    assert (second["id"], second["revision"], second["text_body"]) == (first["id"], 2, "print(2)")

    empty = await submit_text(api, campus, eid, homework, "   ", 2)
    assert empty.status_code == 422
    # The notes lesson isn't an assignment; another student and the grader: not theirs -> 404.
    notes = await api.request("GET", mine(eid, course.lesson_ids[0]), campus.cse, campus.c)
    assert notes.status_code == 404
    other = await api.request("GET", mine(eid, homework), campus.ece, campus.c)
    assert other.status_code == 404
    instructor = await api.request("GET", mine(eid, homework), campus.c_instructor, campus.c)
    assert instructor.status_code == 404


async def test_kind_must_be_allowed(api: CourseApi, campus: Campus) -> None:
    course, eid, homework = await assignment_course(api, campus)
    ok(
        await api.define_assignment(
            campus.author, campus.p, course.id, homework, submission_kinds=["file"]
        )
    )
    ok(await api.publish(campus.author, campus.p, course.id, "major"), 201)
    # The college opts its enrollments into major 2, which accepts files only.
    ok(
        await api.request(
            "POST",
            f"/courses/{course.id}/enrollment-upgrades",
            campus.c_admin,
            campus.c,
            json={"to_major": 2},
        ),
        202,
    )
    await api.run_jobs()
    refused = await submit_text(api, campus, eid, homework, "print(1)", 0)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "submission_kind_not_allowed"


async def test_file_submission_through_minio(api: CourseApi, campus: Campus) -> None:
    course, eid, homework = await assignment_course(api, campus)
    created = ok(
        await api.request(
            "POST",
            mine(eid, homework, "submission-upload"),
            campus.cse,
            campus.c,
            json={"file_name": "fizzbuzz.pdf", "content_type": "application/pdf"},
        ),
        201,
    )
    assert created["file"]["kind"] == "submission"
    async with httpx.AsyncClient() as http:
        upload = created["upload"]
        posted = await http.post(upload["url"], data=upload["fields"], files={"file": ("f", PDF)})
        assert posted.status_code == 204, posted.text
    submitted = ok(
        await api.request(
            "PUT",
            mine(eid, homework, "submission"),
            campus.cse,
            campus.c,
            json={"submission": {"kind": "file", "file_id": created["file"]["id"]}},
            headers={"If-Match": "0"},
        )
    )
    assert submitted["kind"] == "file"
    assert submitted["file"]["file_name"] == "fizzbuzz.pdf"
    async with httpx.AsyncClient() as http:
        assert (await http.get(submitted["file"]["url"])).content == PDF

    # The grader can open it; another student's upload id isn't usable by anyone else.
    queue = ok(
        await api.request(
            "GET",
            f"/courses/{course.id}/lessons/{homework}/submissions",
            campus.c_instructor,
            campus.c,
        )
    )
    detail = ok(
        await api.request(
            "GET",
            f"/assignment-submissions/{queue['items'][0]['id']}",
            campus.c_instructor,
            campus.c,
        )
    )
    assert detail["submission"]["file"]["url"]


async def test_rejected_upload_cannot_be_submitted(api: CourseApi, campus: Campus) -> None:
    _, eid, homework = await assignment_course(api, campus)
    created = ok(
        await api.request(
            "POST",
            mine(eid, homework, "submission-upload"),
            campus.cse,
            campus.c,
            json={"file_name": "fake.pdf", "content_type": "application/pdf"},
        ),
        201,
    )
    async with httpx.AsyncClient() as http:
        upload = created["upload"]
        await http.post(
            upload["url"], data=upload["fields"], files={"file": ("f", b"MZ not a pdf")}
        )
    response = await api.request(
        "PUT",
        mine(eid, homework, "submission"),
        campus.cse,
        campus.c,
        json={"submission": {"kind": "file", "file_id": created["file"]["id"]}},
        headers={"If-Match": "0"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "file_rejected"
    assert (
        ok(await api.request("GET", mine(eid, homework), campus.cse, campus.c))["submission"]
        is None
    )


# ---------------------------------------------------------------------------- grading


async def test_grading_completes_the_lesson_and_progress(api: CourseApi, campus: Campus) -> None:
    course, eid, homework = await assignment_course(api, campus)
    notes = course.lesson_ids[0]
    ok(
        await api.request(
            "POST", f"/enrollments/{eid}/lessons/{notes}/complete", campus.cse, campus.c
        )
    )
    submission = ok(await submit_text(api, campus, eid, homework, "for i in range(1, 101): ...", 0))
    assert (
        ok(await api.request("GET", f"/enrollments/{eid}", campus.cse, campus.c))["enrollment"][
            "progress_percent"
        ]
        == 50
    )

    queue_path = f"/courses/{course.id}/lessons/{homework}/submissions"
    queue = ok(await api.request("GET", queue_path, campus.c_instructor, campus.c))
    [row] = queue["items"]
    assert (row["status"], row["student"]["id"], row["score"]) == (
        "submitted",
        str(campus.cse.id),
        None,
    )

    sid = submission["id"]
    assert (await grade(api, campus.c_instructor, campus.c, sid, None)).status_code == 428
    assert (await grade(api, campus.c_instructor, campus.c, sid, 9)).status_code == 409
    too_high = await grade(api, campus.c_instructor, campus.c, sid, 1, score="10.5")
    assert too_high.status_code == 422
    assert too_high.json()["error"]["code"] == "score_out_of_range"
    graded = ok(await grade(api, campus.c_instructor, campus.c, sid, 1, score="8.5"))
    assert graded["submission"]["status"] == "graded"
    assert graded["submission"]["grade"]["score"] == "8.50"

    seen = ok(await api.request("GET", mine(eid, homework), campus.cse, campus.c))
    assert seen["submission"]["grade"] == {
        "score": "8.50",
        "max_marks": 10,
        "feedback": "Good",
        "graded_at": seen["submission"]["grade"]["graded_at"],
        "graded_by": str(campus.c_instructor.id),
    }
    detail = ok(await api.request("GET", f"/enrollments/{eid}", campus.cse, campus.c))
    assert detail["enrollment"]["progress_percent"] == 100
    assert detail["enrollment"]["completed_at"]
    progress = {p["lesson_id"]: p["status"] for p in detail["progress"]}
    assert progress[str(homework)] == "completed"
    assert await lesson_completed_events(api, homework) == 1

    # Grading is final for the student; the grader may correct it (one more completion: none).
    again = await submit_text(api, campus, eid, homework, "print(3)", 2)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_graded"
    regraded = ok(await grade(api, campus.c_admin, campus.c, sid, 2, score="9", feedback="Better"))
    assert regraded["submission"]["grade"]["score"] == "9.00"
    assert await lesson_completed_events(api, homework) == 1

    audit = ok(
        await api.request(
            "GET", "/audit-log", campus.c_admin, campus.c, params={"action": "assignment.graded"}
        )
    )
    assert [e["target_id"] for e in audit["items"]] == [sid, sid]
    assert audit["items"][0]["before"]["score"] == "8.50"


async def test_queue_order_filters_and_pagination(api: CourseApi, campus: Campus) -> None:
    students = [campus.cse]
    for _ in range(2):  # in CSE before the course is assigned, so fan-out enrolls them
        student = await api.factory.member(campus.c, "student")
        await api.factory.add_to_batch(campus.cse_batch, student)
        students.append(student)
    course, _eid, homework = await assignment_course(api, campus)
    ids = []
    for student in students:
        enrollment = await api.enrollment_for(student, campus.c, course.id)
        assert enrollment
        response = await api.request(
            "PUT",
            mine(enrollment["id"], homework, "submission"),
            student,
            campus.c,
            json={"submission": {"kind": "text", "text": "x"}},
            headers={"If-Match": "0"},
        )
        ids.append(ok(response)["id"])
    ok(await grade(api, campus.c_instructor, campus.c, ids[0], 1))

    path = f"/courses/{course.id}/lessons/{homework}/submissions"
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = ok(await api.request("GET", path, campus.c_instructor, campus.c, params=params))
        seen += [r["id"] for r in page["items"]]
        if not (cursor := page["next_cursor"]):
            break
    assert seen == [ids[1], ids[2], ids[0]]  # ungraded first, oldest first; graded last
    graded = ok(
        await api.request("GET", path, campus.c_instructor, campus.c, params={"status": "graded"})
    )
    assert [r["id"] for r in graded["items"]] == [ids[0]]
    ece_batch = ok(
        await api.request(
            "GET",
            path,
            campus.c_instructor,
            campus.c,
            params={"batch_id": str(campus.ece_batch.id)},
        )
    )
    assert ece_batch["items"] == []
    elsewhere = await api.request(
        "GET", path, campus.c_instructor, campus.c, params={"batch_id": str(campus.o_batch.id)}
    )
    assert elsewhere.status_code == 404


async def test_only_the_students_org_grades(api: CourseApi, campus: Campus) -> None:
    course, eid, homework = await assignment_course(api, campus)
    sid = ok(await submit_text(api, campus, eid, homework, "x", 0))["id"]
    queue = f"/courses/{course.id}/lessons/{homework}/submissions"
    # The publisher owns the course but sees no other org's work; the unrelated org can't read it.
    assert ok(await api.request("GET", queue, campus.author, campus.p))["items"] == []
    assert (await api.request("GET", queue, campus.o_admin, campus.o)).status_code == 404
    for user, org in (
        (campus.author, campus.p),
        (campus.p_admin, campus.p),
        (campus.o_admin, campus.o),
    ):
        assert (
            await api.request("GET", f"/assignment-submissions/{sid}", user, org)
        ).status_code == 404
        assert (await grade(api, user, org, sid, 1)).status_code == 404
    # Students can't grade (403: no permission), not even their own.
    assert (await grade(api, campus.cse, campus.c, sid, 1)).status_code == 403
