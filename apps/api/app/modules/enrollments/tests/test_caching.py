"""Version-outline cache and heartbeat-check cache: hits avoid Postgres, authorization never
does, and a publish invalidates the pointer after commit."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import event

from app.modules.courses.cache import pointer_key, version_key
from app.modules.enrollments.heartbeat_cache import check_key
from tests.course_api import BuiltCourse, Campus, CourseApi, ok, published_for_cse


@contextmanager
def statements(api: CourseApi) -> Iterator[list[str]]:
    """Every SQL statement the app runs inside the block."""
    seen: list[str] = []
    engine = api.app.state.engine.sync_engine

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        seen.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def touching(seen: list[str], table: str) -> list[str]:
    return [s for s in seen if f" {table}" in s or f'"{table}"' in s]


async def setup(api: CourseApi, campus: Campus, **build: Any) -> tuple[BuiltCourse, str]:
    course = await published_for_cse(api, campus, **build)
    enrollment = await api.enrollment_for(campus.cse, campus.c, course.id)
    assert enrollment
    return course, enrollment["id"]


async def test_outline_is_served_from_cache_but_still_authorized(
    api: CourseApi, campus: Campus
) -> None:
    course, eid = await setup(api, campus)
    redis = api.app.state.redis
    ok(await api.request("GET", f"/enrollments/{eid}", campus.cse, campus.c))  # warms the cache
    pointer = await redis.get(pointer_key(course.id, 1))
    assert pointer is not None
    assert await redis.exists(version_key(UUID(pointer)))

    with statements(api) as seen:
        detail = ok(await api.request("GET", f"/enrollments/{eid}", campus.cse, campus.c))
    assert detail["outline"]["modules"]  # served
    assert touching(seen, "course_versions") == []  # snapshot and lessons came from Redis
    assert touching(seen, "course_version_lessons") == []
    assert any("app.course_readable" in s for s in seen)  # ...but readability was checked

    # Revoking the batch assignment hides the course at once, cache or not.
    assignments = ok(
        await api.request("GET", f"/courses/{course.id}/assignments", campus.c_admin, campus.c)
    )
    row = next(a for a in assignments["items"] if a["batch_id"] == str(campus.cse_batch.id))
    removed = await api.request(
        "DELETE", f"/course-assignments/{row['id']}", campus.c_admin, campus.c
    )
    ok(removed, 204)
    gone = await api.request("GET", f"/enrollments/{eid}", campus.cse, campus.c)
    assert gone.status_code == 404


async def test_publish_invalidates_the_pointer_after_commit(api: CourseApi, campus: Campus) -> None:
    course, eid = await setup(api, campus)
    redis = api.app.state.redis
    ok(await api.request("GET", f"/enrollments/{eid}", campus.cse, campus.c))
    old = await redis.get(pointer_key(course.id, 1))

    ok(
        await api.request(
            "PATCH", f"/courses/{course.id}/lessons/{course.lesson_ids[0]}", campus.author,
            campus.p, json={"title": "Renamed in 1.1"},
        )
    )  # fmt: skip
    ok(await api.publish(campus.author, campus.p, course.id, "minor"), 201)
    assert await redis.get(pointer_key(course.id, 1)) is None  # deleted after the commit
    assert await redis.exists(version_key(UUID(old)))  # immutable 1.0 entry can stay

    detail = ok(await api.request("GET", f"/enrollments/{eid}", campus.cse, campus.c))
    assert detail["version"]["version"] == "1.1"
    titles = [lesson["title"] for m in detail["outline"]["modules"] for lesson in m["lessons"]]
    assert "Renamed in 1.1" in titles
    assert await redis.get(pointer_key(course.id, 1)) == detail["version"]["id"]


async def heartbeat(api: CourseApi, campus: Campus, eid: str, lesson: UUID, asset: str,
                    position: int) -> int:  # fmt: skip
    response = await api.request(
        "POST", "/progress/heartbeat", campus.cse, campus.c,
        json={"enrollment_id": eid, "lesson_id": str(lesson), "video_asset_id": asset,
              "position_seconds": position, "played_seconds": 15},
    )  # fmt: skip
    return response.status_code


async def test_steady_heartbeats_touch_only_redis(api: CourseApi, campus: Campus) -> None:
    course, eid = await setup(api, campus, modules=(("video",),))
    lesson = course.lesson_ids[0]
    asset = ok(
        await api.request("GET", f"/enrollments/{eid}/lessons/{lesson}/resume", campus.cse,
                          campus.c)
    )["video_asset_id"]  # fmt: skip
    assert await heartbeat(api, campus, eid, lesson, asset, 15) == 204  # validates, seeds buffer
    assert await api.app.state.redis.exists(check_key(UUID(eid), lesson))

    with statements(api) as seen:
        assert await heartbeat(api, campus, eid, lesson, asset, 30) == 204
    app_queries = [s for s in seen if not s.startswith(("SELECT set_config", "BEGIN", "COMMIT"))]
    assert app_queries == [], app_queries  # only the per-request RLS context, no reads


async def test_heartbeat_cache_is_per_user_and_follows_new_releases(
    api: CourseApi, campus: Campus
) -> None:
    course, eid = await setup(api, campus, modules=(("video",),))
    lesson = course.lesson_ids[0]
    asset = ok(
        await api.request("GET", f"/enrollments/{eid}/lessons/{lesson}/resume", campus.cse,
                          campus.c)
    )["video_asset_id"]  # fmt: skip
    assert await heartbeat(api, campus, eid, lesson, asset, 15) == 204

    # Another student using a cached enrollment id gets nothing from the cache.
    stolen = await api.request(
        "POST", "/progress/heartbeat", campus.ece, campus.c,
        json={"enrollment_id": eid, "lesson_id": str(lesson), "video_asset_id": asset,
              "position_seconds": 30, "played_seconds": 15},
    )  # fmt: skip
    assert stolen.status_code == 404

    # A minor release replacing the video is seen on the very next heartbeat (no TTL wait).
    replacement = await api.factory.video(campus.p, status="ready", duration=120)
    ok(
        await api.request(
            "PATCH", f"/courses/{course.id}/lessons/{lesson}", campus.author, campus.p,
            json={"content": {"video_asset_id": str(replacement.id)}},
        )
    )  # fmt: skip
    ok(await api.publish(campus.author, campus.p, course.id, "minor"), 201)
    assert await heartbeat(api, campus, eid, lesson, asset, 30) == 409
    assert await heartbeat(api, campus, eid, lesson, str(replacement.id), 15) == 204
