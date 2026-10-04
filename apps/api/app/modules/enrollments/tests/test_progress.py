"""Completion rules and course progress, through the student API."""

from decimal import Decimal
from uuid import UUID

import pytest
from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.courses.models import LessonType
from app.modules.enrollments.progress import (
    CompletionRule,
    completion_rule,
    course_percent,
    video_watched,
)
from app.modules.identity.models import Organization, User
from tests.course_api import Campus, CourseApi, ok, published_for_cse

# ---------------------------------------------------------------------------- pure rules


@pytest.mark.parametrize(
    ("done", "required", "percent"),
    [(0, 3, 0), (1, 3, 33), (2, 3, 66), (3, 3, 100), (199, 200, 99), (0, 0, 0), (5, 3, 100)],
)
def test_course_percent_rounds_down(done: int, required: int, percent: int) -> None:
    assert course_percent(done, required) == percent


def test_completion_rules() -> None:
    assert completion_rule(LessonType.VIDEO) is CompletionRule.WATCHED
    assert completion_rule(LessonType.NOTES) is CompletionRule.MANUAL
    assert completion_rule(LessonType.PDF) is CompletionRule.MANUAL_AFTER_OPENING
    # Phase 2.5: assignments complete when graded (no longer placeholders).
    assert completion_rule(LessonType.ASSIGNMENT) is CompletionRule.GRADED
    for placeholder in (LessonType.QUIZ, LessonType.LAB):
        assert completion_rule(placeholder) is CompletionRule.NOT_COMPLETABLE


def test_video_watched_threshold() -> None:
    assert video_watched(Decimal("0.90"), None)
    assert not video_watched(Decimal("0.89"), None)
    assert video_watched(Decimal("0.50"), Decimal("0.50"))
    assert not video_watched(None, None)


# ---------------------------------------------------------------------------- API


async def _complete(
    api: CourseApi, student: User, org: Organization, enrollment: str, lesson: UUID
) -> Response:
    return await api.request(
        "POST", f"/enrollments/{enrollment}/lessons/{lesson}/complete", student, org
    )


async def _open_pdf(
    owner: async_sessionmaker[AsyncSession],
    enrollment: str,
    lesson: UUID,
    student: User,
    org: Organization,
) -> None:
    """Stand-in for issuing a signed PDF URL (the PDF step records `pdf_opened_at` then)."""
    async with owner() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO lesson_progress (enrollment_id, lesson_id, organization_id, user_id, "
                "status, pdf_opened_at) VALUES (:e, :l, :o, :u, 'in_progress', now())"
            ),
            {"e": enrollment, "l": lesson, "o": org.id, "u": student.id},
        )  # fmt: skip


async def test_student_completes_course_with_correct_progress(
    api: CourseApi, campus: Campus, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    course = await published_for_cse(api, campus, modules=[("notes", "pdf", "lab"), ("notes",)])
    notes1, pdf, lab, notes2 = course.lesson_ids
    enrollment = (await api.enrollments(campus.cse, campus.c))[0]["id"]

    first = ok(await _complete(api, campus.cse, campus.c, enrollment, notes1))
    assert first["enrollment"]["progress_percent"] == 33  # 1 of 3 required (lab excluded)
    assert first["lesson"]["status"] == "completed"
    again = ok(await _complete(api, campus.cse, campus.c, enrollment, notes1))
    assert again["enrollment"]["progress_percent"] == 33  # idempotent

    unopened = await _complete(api, campus.cse, campus.c, enrollment, pdf)
    assert (unopened.status_code, unopened.json()["error"]["code"]) == (409, "pdf_not_opened")
    placeholder = await _complete(api, campus.cse, campus.c, enrollment, lab)
    assert placeholder.json()["error"]["code"] == "not_completable"

    await _open_pdf(owner_sessionmaker, enrollment, pdf, campus.cse, campus.c)
    assert (
        ok(await _complete(api, campus.cse, campus.c, enrollment, pdf))["enrollment"][
            "progress_percent"
        ]
        == 66
    )
    done = ok(await _complete(api, campus.cse, campus.c, enrollment, notes2))["enrollment"]
    assert done["progress_percent"] == 100
    assert done["completed_at"] is not None

    detail = ok(await api.request("GET", f"/enrollments/{enrollment}", campus.cse, campus.c))
    completed = {p["lesson_id"] for p in detail["progress"] if p["status"] == "completed"}
    assert completed == {str(notes1), str(pdf), str(notes2)}
    assert detail["version"]["version"] == "1.0"
    assert [len(m["lessons"]) for m in detail["outline"]["modules"]] == [3, 1]

    async with owner_sessionmaker() as s:
        events = await s.execute(
            text(
                "SELECT payload->>'lesson_id', (payload->>'progress_percent')::int "
                "FROM outbox_events WHERE event_type = 'lesson_completed' "
                "AND aggregate_id = :e ORDER BY occurred_at, id"
            ),
            {"e": enrollment},
        )
        assert [(lid, pct) for lid, pct in events] == [
            (str(notes1), 33), (str(pdf), 66), (str(notes2), 100)
        ]  # fmt: skip


async def test_videos_complete_by_watching(api: CourseApi, campus: Campus) -> None:
    course = await published_for_cse(api, campus, modules=[("video",)])
    enrollment = (await api.enrollments(campus.cse, campus.c))[0]["id"]
    response = await _complete(api, campus.cse, campus.c, enrollment, course.lesson_ids[0])
    assert response.json()["error"]["code"] == "completed_by_watching"


async def test_visit_drives_continue_learning(api: CourseApi, campus: Campus) -> None:
    course = await published_for_cse(api, campus, modules=[("notes", "notes")])
    enrollment = (await api.enrollments(campus.cse, campus.c))[0]
    assert enrollment["last_lesson_id"] is None

    visited = ok(
        await api.request(
            "POST", f"/enrollments/{enrollment['id']}/lessons/{course.lesson_ids[1]}/visit",
            campus.cse, campus.c,
        )
    )  # fmt: skip
    detail = ok(await api.request("GET", f"/enrollments/{enrollment['id']}", campus.cse, campus.c))

    assert visited["last_lesson_id"] == str(course.lesson_ids[1])
    assert visited["last_accessed_at"] is not None
    assert [(p["lesson_id"], p["status"]) for p in detail["progress"]] == [
        (str(course.lesson_ids[1]), "in_progress")
    ]


async def test_students_only_reach_their_own_enrollments(api: CourseApi, campus: Campus) -> None:
    course = await published_for_cse(api, campus, modules=[("notes",)])
    enrollment = (await api.enrollments(campus.cse, campus.c))[0]["id"]
    lesson = course.lesson_ids[0]

    for user, org in (
        (campus.ece, campus.c),
        (campus.c_admin, campus.c),
        (campus.o_admin, campus.o),
    ):
        detail = await api.request("GET", f"/enrollments/{enrollment}", user, org)
        complete = await _complete(api, user, org, enrollment, lesson)
        assert (detail.status_code, complete.status_code) == (404, 404)
    # A lesson that isn't in the student's version is a 404 too.
    other = await _complete(api, campus.cse, campus.c, enrollment, UUID(int=3))
    assert other.status_code == 404


async def test_enrollments_filter_by_course(api: CourseApi, campus: Campus) -> None:
    mine = await published_for_cse(api, campus, modules=(("notes",),))
    other = await published_for_cse(api, campus, modules=(("notes",),))

    async def ids(course_id: object) -> list[str]:
        page = ok(
            await api.request("GET", f"/enrollments?course_id={course_id}", campus.cse, campus.c)
        )
        return [e["course_id"] for e in page["items"]]

    assert await ids(mine.id) == [str(mine.id)]
    assert await ids(other.id) == [str(other.id)]
    # Another student of the same org (not in the batch) has no enrollment in it.
    page = ok(await api.request("GET", f"/enrollments?course_id={mine.id}", campus.ece, campus.c))
    assert page["items"] == []
