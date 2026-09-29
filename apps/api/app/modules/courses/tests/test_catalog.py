"""Public catalog: entries on publish, anonymous reads, revalidation after commit (respx)."""

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings
from app.modules.courses.jobs import REVALIDATE_CATALOG
from app.modules.courses.tasks import RevalidationError, revalidate_catalog
from tests.course_api import Campus, CourseApi, ok
from tests.fakes import RecordingJobQueue


def revalidations(queue: RecordingJobQueue) -> int:
    return sum(1 for task, _ in queue.sent if task == REVALIDATE_CATALOG)


async def test_public_courses_appear_on_publish_and_leave_on_archive(
    api: CourseApi, campus: Campus
) -> None:
    public = await api.build(campus.author, campus.p, [("notes",)], title="Public DSA", public=True)
    private = await api.build(campus.author, campus.p, [("notes",)], title="Private DSA")
    for course in (public, private):
        ok(await api.publish(campus.author, campus.p, course.id), 201)

    listed = ok(await api.client.get("/api/v1/catalog?limit=100"))  # anonymous
    titles = {e["title"]: e for e in listed["items"]}
    assert "Public DSA" in titles
    assert "Private DSA" not in titles
    entry = titles["Public DSA"]
    assert set(entry) == {
        "slug", "title", "description", "skill_names", "lesson_count", "published_at",
        "updated_at",
    }  # public fields only: no ids, no organization  # fmt: skip
    assert entry["lesson_count"] == 1
    assert ok(await api.client.get(f"/api/v1/catalog/{entry['slug']}"))["title"] == "Public DSA"

    ok(await api.request("DELETE", f"/courses/{public.id}", campus.author, campus.p))
    assert (await api.client.get(f"/api/v1/catalog/{entry['slug']}")).status_code == 404


async def test_catalog_slug_is_validated(api: CourseApi) -> None:
    assert (await api.client.get("/api/v1/catalog/NOT_A_SLUG")).status_code == 422
    assert (await api.client.get("/api/v1/catalog/unknown-course")).status_code == 404


async def test_revalidation_is_enqueued_only_after_a_committed_publish(
    api: CourseApi, campus: Campus
) -> None:
    queue: RecordingJobQueue = api.app.state.jobs
    empty = await api.build(campus.author, campus.p, [()], public=True)
    blocked = await api.publish(campus.author, campus.p, empty.id)
    assert blocked.status_code == 409  # empty course: the transaction rolls back
    assert revalidations(queue) == 0

    course = await api.build(campus.author, campus.p, [("notes",)], public=True)
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    assert revalidations(queue) == 1
    ok(await api.request("DELETE", f"/courses/{course.id}", campus.author, campus.p))
    assert revalidations(queue) == 2


def configured(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "web_internal_url": "http://web.test:3000/",
            "revalidate_secret": SecretStr("s3cr3t-for-tests"),
        }
    )


@respx.mock
async def test_revalidation_calls_the_web_app_with_the_secret(settings: Settings) -> None:
    route = respx.post("http://web.test:3000/api/revalidate").respond(200, json={})
    async with httpx.AsyncClient() as http:
        assert await revalidate_catalog(configured(settings), http) is True
    request = route.calls.last.request
    assert request.headers["x-revalidate-secret"] == "s3cr3t-for-tests"
    assert request.read() == b'{"tags":["catalog"]}'


@respx.mock
@pytest.mark.parametrize("failure", [500, 401, httpx.ConnectError("refused")])
async def test_failed_revalidation_raises_so_celery_retries(
    settings: Settings, failure: int | Exception
) -> None:
    route = respx.post("http://web.test:3000/api/revalidate")
    if isinstance(failure, int):
        route.respond(failure)
    else:
        route.side_effect = failure
    async with httpx.AsyncClient() as http:
        with pytest.raises(RevalidationError):
            await revalidate_catalog(configured(settings), http)


async def test_revalidation_is_skipped_when_not_configured(settings: Settings) -> None:
    unset = settings.model_copy(update={"web_internal_url": None, "revalidate_secret": None})
    async with httpx.AsyncClient() as http:
        assert await revalidate_catalog(unset, http) is False
