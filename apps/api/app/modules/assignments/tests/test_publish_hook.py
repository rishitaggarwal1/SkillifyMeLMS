"""The publish hook for assignment content (app.wiring + courses.content_sources) fails closed:
with no registered source, a course with an assignment lesson can't be previewed or published;
nothing is silently skipped. Entry points that never call `wire()` (workers, seeds) still get the
hook, because `content_sources` loads the wiring on first use."""

import pytest

from app.modules.assignments.service import AssignmentContentSource
from app.modules.courses import content_sources
from app.modules.courses.models import LessonType
from tests.course_api import Campus, CourseApi, ok


@pytest.fixture
def unwired(monkeypatch: pytest.MonkeyPatch) -> None:
    """No source registered, and loading the wiring registers nothing (a broken deployment)."""
    monkeypatch.setattr(content_sources, "_SOURCES", {})
    monkeypatch.setattr(content_sources, "_load_wiring", lambda: None)


@pytest.mark.usefixtures("unwired")
async def test_publishing_fails_closed_without_the_hook(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, modules=(("notes",),))
    lesson = ok(
        await api.request(
            "POST",
            f"/courses/{course.id}/modules/{course.module_ids[0]}/lessons",
            campus.author,
            campus.p,
            json={"title": "Homework", "lesson_type": "assignment"},
        ),
        201,
    )
    assert lesson["lesson_type"] == "assignment"
    for response in (
        await api.request("GET", f"/courses/{course.id}/publish-preview", campus.author, campus.p),
        await api.publish(campus.author, campus.p, course.id),
    ):
        assert response.status_code == 500, response.text
        error = response.json()["error"]
        assert error["code"] == "content_source_not_registered"
        assert error["details"] == {"lesson_type": "assignment"}
    versions = ok(
        await api.request("GET", f"/courses/{course.id}/versions", campus.author, campus.p)
    )
    assert versions["items"] == []  # nothing was published


@pytest.mark.usefixtures("unwired")
async def test_courses_without_assignments_dont_need_the_hook(
    api: CourseApi, campus: Campus
) -> None:
    course = await api.build(campus.author, campus.p, modules=(("notes",),))
    ok(await api.publish(campus.author, campus.p, course.id), 201)


def test_the_wiring_loads_on_first_use(monkeypatch: pytest.MonkeyPatch) -> None:
    # As in a Celery worker or a CLI seed: nothing called wire(), the registry starts empty.
    monkeypatch.setattr(content_sources, "_SOURCES", {})
    source = content_sources.required_source(LessonType.ASSIGNMENT)
    assert isinstance(source, AssignmentContentSource)
