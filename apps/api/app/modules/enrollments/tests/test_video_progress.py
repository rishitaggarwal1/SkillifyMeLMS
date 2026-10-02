"""Real Redis/Postgres integration, including cross-org playback and crash recovery."""

from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.db.outbox import OutboxEvent
from app.modules.enrollments import tasks, video_buffer
from app.modules.enrollments.repository import LessonProgressRepository
from app.modules.enrollments.video_buffer import BufferedVideo, VideoBuffer, buffer_key
from app.modules.enrollments.video_flush import FlushResult, flush_video_progress
from app.modules.media.models import VideoAsset
from tests.course_api import Campus, CourseApi, ok, published_for_cse
from tests.fixtures import TenantSessionFactory


class FakeClock:
    """Replaces the buffer's wall clock: each heartbeat advances time by `step` seconds."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.step = 15.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(video_buffer, "_now", fake)
    return fake


async def video_setup(api: CourseApi, campus: Campus) -> tuple[str, str, str]:
    course = await published_for_cse(api, campus, modules=(("video",),))
    enrollment = await api.enrollment_for(campus.cse, campus.c, course.id)
    assert enrollment
    eid, lid = enrollment["id"], str(course.lesson_ids[0])
    resume = ok(
        await api.request("GET", f"/enrollments/{eid}/lessons/{lid}/resume", campus.cse, campus.c)
    )
    return eid, lid, resume["video_asset_id"]


async def heartbeat(
    api: CourseApi,
    campus: Campus,
    ids: tuple[str, str, str],
    position: float,
    played: float = 15,
    rate: float = 1,
    expect: int = 204,
) -> None:
    eid, lid, asset = ids
    response = await api.request(
        "POST",
        "/progress/heartbeat",
        campus.cse,
        campus.c,
        json={
            "enrollment_id": eid,
            "lesson_id": lid,
            "video_asset_id": asset,
            "position_seconds": position,
            "played_seconds": played,
            "playback_rate": rate,
        },
    )
    assert response.status_code == expect, response.text


async def watched(api: CourseApi, campus: Campus, ids: tuple[str, str, str]) -> float:
    path = f"/enrollments/{ids[0]}/lessons/{ids[1]}/resume"
    ratio: float = ok(await api.request("GET", path, campus.cse, campus.c))["watched_ratio"]
    return ratio


async def test_heartbeat_flush_resume_and_completion(api: CourseApi, campus: Campus) -> None:
    ids = await video_setup(api, campus)
    eid, lid, asset = ids
    redis, sessions = api.app.state.redis, api.app.state.sessionmaker
    for position in range(15, 121, 15):
        await heartbeat(api, campus, ids, position)
    resume_path = f"/enrollments/{eid}/lessons/{lid}/resume"
    resume = ok(await api.request("GET", resume_path, campus.cse, campus.c))
    assert resume["position_seconds"] == 120
    assert resume["watched_ratio"] == 1
    assert (await flush_video_progress(sessions, redis)).changed >= 1
    assert (await flush_video_progress(sessions, redis)).changed == 0
    key = buffer_key(UUID(eid), UUID(lid), UUID(asset))
    await redis.delete(key, f"{key}:bits")
    assert ok(await api.request("GET", resume_path, campus.cse, campus.c)) == resume
    detail = ok(await api.request("GET", f"/enrollments/{eid}", campus.cse, campus.c))
    assert detail["enrollment"]["progress_percent"] == 100
    assert detail["enrollment"]["completed_at"]
    assert detail["enrollment"]["last_lesson_id"] == lid


async def test_seek_and_authorization(api: CourseApi, campus: Campus) -> None:
    ids = await video_setup(api, campus)
    eid, lid, _ = ids
    await heartbeat(api, campus, ids, 110, 0)
    path = f"/enrollments/{eid}/lessons/{lid}"
    assert (
        ok(await api.request("GET", path + "/resume", campus.cse, campus.c))["watched_ratio"] == 0
    )
    assert ok(await api.request("GET", path + "/playback", campus.cse, campus.c))["kind"] == "mp4"
    for user, org in [
        (campus.ece, campus.c),
        (campus.o_admin, campus.o),
        (campus.c_admin, campus.c),
    ]:
        assert (await api.request("GET", path + "/playback", user, org)).status_code == 404
    for bad in [-1, 1210]:
        response = await api.request(
            "POST",
            "/progress/heartbeat",
            campus.cse,
            campus.c,
            json={
                "enrollment_id": eid,
                "lesson_id": lid,
                "video_asset_id": ids[2],
                "position_seconds": bad,
                "played_seconds": 15,
            },
        )
        assert response.status_code == 422


async def test_video_rls_and_immediate_revocation(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    ids = await video_setup(api, campus)
    # c_admin: staff of an assigned org read the content (migration 0008); ece: not in the batch.
    for user, visible in [(campus.cse, True), (campus.ece, False), (campus.c_admin, True)]:
        async with tenant_session(org=campus.c.id, user=user.id) as session:
            found = await session.scalar(select(VideoAsset.id).where(VideoAsset.id == UUID(ids[2])))
            assert (found is not None) == visible
    detail = ok(await api.request("GET", f"/enrollments/{ids[0]}", campus.cse, campus.c))
    cid = detail["enrollment"]["course_id"]
    assignments = ok(
        await api.request("GET", f"/courses/{cid}/assignments", campus.c_admin, campus.c)
    )
    batch = next(a for a in assignments["items"] if a["batch_id"] == str(campus.cse_batch.id))
    response = await api.request(
        "DELETE", f"/course-assignments/{batch['id']}", campus.c_admin, campus.c
    )
    assert response.is_success
    # Do not run reconciliation: RLS must revoke immediately, before the worker catches up.
    assert (
        await api.request(
            "GET", f"/enrollments/{ids[0]}/lessons/{ids[1]}/playback", campus.cse, campus.c
        )
    ).status_code == 404
    async with tenant_session(org=campus.c.id, user=campus.cse.id) as session:
        assert (
            await session.scalar(select(VideoAsset.id).where(VideoAsset.id == UUID(ids[2]))) is None
        )


async def test_flush_commit_before_ack_is_idempotent(
    api: CourseApi, campus: Campus, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await video_setup(api, campus)
    await heartbeat(api, campus, ids, 15)
    redis, sessions = api.app.state.redis, api.app.state.sessionmaker
    original = VideoBuffer.acknowledge

    async def fail_ack(self: VideoBuffer, entries: object) -> None:
        raise ConnectionError("simulated Redis outage after commit")

    monkeypatch.setattr(VideoBuffer, "acknowledge", fail_ack)
    with pytest.raises(ConnectionError):
        await flush_video_progress(sessions, redis)
    monkeypatch.setattr(VideoBuffer, "acknowledge", original)
    assert (await flush_video_progress(sessions, redis)).changed == 0
    async with api.factory.sessionmaker() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == UUID(ids[0]),
                OutboxEvent.event_type == "video_progress",
            )
        )
        assert count == 1


async def test_new_heartbeat_during_ack_remains_dirty(
    api: CourseApi, campus: Campus, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await video_setup(api, campus)
    await heartbeat(api, campus, ids, 15)
    original = VideoBuffer.acknowledge

    async def race(self: VideoBuffer, entries: list[BufferedVideo]) -> None:
        await heartbeat(api, campus, ids, 30)
        await original(self, entries)

    monkeypatch.setattr(VideoBuffer, "acknowledge", race)
    await flush_video_progress(api.app.state.sessionmaker, api.app.state.redis)
    monkeypatch.setattr(VideoBuffer, "acknowledge", original)
    flushed = await flush_video_progress(api.app.state.sessionmaker, api.app.state.redis)
    assert flushed.changed == 1


async def test_pause_inside_segment_keeps_watched_time(api: CourseApi, campus: Campus) -> None:
    ids = await video_setup(api, campus)
    await heartbeat(api, campus, ids, 3, 3)
    await heartbeat(api, campus, ids, 15, 12)
    resume = ok(
        await api.request(
            "GET",
            f"/enrollments/{ids[0]}/lessons/{ids[1]}/resume",
            campus.cse,
            campus.c,
        )
    )
    assert resume["watched_ratio"] == 15 / 120


async def test_sub_second_jitter_still_completes_a_segment(api: CourseApi, campus: Campus) -> None:
    """Playback "from 0" can first be measured a few milliseconds in (seen in the done-when e2e:
    a first interval of [0.0036, 5] left segment 0 unwatched forever). Gaps up to
    SEGMENT_GAP_SECONDS count as watched; a real skip doesn't."""
    ids = await video_setup(api, campus)
    await heartbeat(api, campus, ids, 15, 15 - 0.0036)  # [0.0036, 15]
    assert await watched(api, campus, ids) == 15 / 120
    await heartbeat(api, campus, ids, 30, 14.8)  # [15.2, 30]: a 0.2 s hole at 15
    assert await watched(api, campus, ids) == 30 / 120
    await heartbeat(api, campus, ids, 45, 14)  # [31, 45]: a 1 s skip is not watching
    assert await watched(api, campus, ids) == 40 / 120  # segment [30, 35) stays unwatched


@pytest.mark.parametrize("completed", [False, True])
async def test_replaced_video_resets_partial_watch_and_rejects_old_player(
    api: CourseApi, campus: Campus, completed: bool
) -> None:
    ids = await video_setup(api, campus)
    for position in range(15, 121 if completed else 16, 15):
        await heartbeat(api, campus, ids, position)
    await flush_video_progress(api.app.state.sessionmaker, api.app.state.redis)
    detail = ok(await api.request("GET", f"/enrollments/{ids[0]}", campus.cse, campus.c))
    cid = detail["enrollment"]["course_id"]
    replacement = await api.factory.video(campus.p, status="ready", duration=120)
    ok(
        await api.request(
            "PATCH",
            f"/courses/{cid}/lessons/{ids[1]}",
            campus.author,
            campus.p,
            json={"content": {"video_asset_id": str(replacement.id)}},
        )
    )
    ok(await api.publish(campus.author, campus.p, UUID(cid), "minor"), 201)
    resume = ok(
        await api.request(
            "GET", f"/enrollments/{ids[0]}/lessons/{ids[1]}/resume", campus.cse, campus.c
        )
    )
    assert resume["position_seconds"] == 0
    assert resume["watched_ratio"] == 0
    detail = ok(await api.request("GET", f"/enrollments/{ids[0]}", campus.cse, campus.c))
    assert (detail["progress"][0]["status"] == "completed") == completed
    stale = await api.request(
        "POST",
        "/progress/heartbeat",
        campus.cse,
        campus.c,
        json={
            "enrollment_id": ids[0],
            "lesson_id": ids[1],
            "video_asset_id": ids[2],
            "position_seconds": 30,
            "played_seconds": 15,
        },
    )
    assert stale.status_code == 409


async def test_database_failure_retains_dirty_heartbeat(
    api: CourseApi, campus: Campus, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await video_setup(api, campus)
    await heartbeat(api, campus, ids, 15)
    original = LessonProgressRepository.upsert_videos

    async def fail(self: LessonProgressRepository, rows: object) -> None:
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(LessonProgressRepository, "upsert_videos", fail)
    with pytest.raises(RuntimeError):
        await flush_video_progress(api.app.state.sessionmaker, api.app.state.redis)
    monkeypatch.setattr(LessonProgressRepository, "upsert_videos", original)
    flushed = await flush_video_progress(api.app.state.sessionmaker, api.app.state.redis)
    assert flushed.changed == 1


async def test_rapid_heartbeats_earn_no_extra_watch_time(
    api: CourseApi, campus: Campus, clock: FakeClock
) -> None:
    ids = await video_setup(api, campus)
    await heartbeat(api, campus, ids, 15)
    clock.step = 0  # a client replaying heartbeats as fast as it can
    for position in range(30, 121, 15):
        await heartbeat(api, campus, ids, position)
    assert await watched(api, campus, ids) == 15 / 120
    clock.step = 2  # 2 s of real time allows at most 3 s of credit at 1x
    await heartbeat(api, campus, ids, 20, 5)
    assert await watched(api, campus, ids) == 15 / 120


@pytest.mark.parametrize(("rate", "ratio"), [(1, 20 / 120), (2, 45 / 120)])
async def test_one_heartbeat_is_capped_by_interval_and_rate(
    api: CourseApi, campus: Campus, rate: float, ratio: float
) -> None:
    ids = await video_setup(api, campus)
    # The cap is 15 s x 1.5 x rate: 22.5 s at 1x (only whole segments 25-45 count), 45 s at 2x.
    await heartbeat(api, campus, ids, 45, 45, rate=rate)
    assert await watched(api, campus, ids) == ratio


@pytest.mark.parametrize("rate", [0.1, 3])
async def test_playback_rate_is_validated(api: CourseApi, campus: Campus, rate: float) -> None:
    ids = await video_setup(api, campus)
    await heartbeat(api, campus, ids, 15, rate=rate, expect=422)


async def test_drain_continues_past_full_batches_with_no_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter([FlushResult(500, 0), FlushResult(500, 3), FlushResult(10, 1)])
    calls = 0

    async def fake_flush(*_: object, **__: object) -> FlushResult:
        nonlocal calls
        calls += 1
        return next(results)

    monkeypatch.setattr(tasks, "flush_video_progress", fake_flush)
    assert await tasks._flush() == 4
    assert calls == 3


async def test_dirty_entries_never_expire_and_flushed_ones_do(
    api: CourseApi, campus: Campus
) -> None:
    ids = await video_setup(api, campus)
    await heartbeat(api, campus, ids, 15)
    redis = api.app.state.redis
    key = buffer_key(UUID(ids[0]), UUID(ids[1]), UUID(ids[2]))
    assert await redis.ttl(key) == -1  # dirty: kept until Postgres has it
    assert await redis.ttl(f"{key}:bits") == -1
    await flush_video_progress(api.app.state.sessionmaker, redis)
    assert 7 * 86400 - 60 < await redis.ttl(key) <= 7 * 86400
    assert 7 * 86400 - 60 < await redis.ttl(f"{key}:bits") <= 7 * 86400


async def test_video_completion_emits_lesson_completed_once(api: CourseApi, campus: Campus) -> None:
    ids = await video_setup(api, campus)
    sessions, redis = api.app.state.sessionmaker, api.app.state.redis
    for position in range(15, 121, 15):
        await heartbeat(api, campus, ids, position)
    await flush_video_progress(sessions, redis)
    await heartbeat(api, campus, ids, 120, 0)  # more heartbeats after completion
    await flush_video_progress(sessions, redis)
    async with api.factory.sessionmaker() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == UUID(ids[0]),
                OutboxEvent.event_type == "lesson_completed",
            )
        )
    assert count == 1


async def test_video_endpoints_reject_non_video_lessons(api: CourseApi, campus: Campus) -> None:
    course = await published_for_cse(api, campus, modules=(("notes",),))
    enrollment = await api.enrollment_for(campus.cse, campus.c, course.id)
    assert enrollment
    path = f"/enrollments/{enrollment['id']}/lessons/{course.lesson_ids[0]}"
    for suffix in ("/resume", "/playback"):
        assert (await api.request("GET", path + suffix, campus.cse, campus.c)).status_code == 404
    response = await api.request(
        "POST", "/progress/heartbeat", campus.cse, campus.c,
        json={"enrollment_id": enrollment["id"], "lesson_id": str(course.lesson_ids[0]),
              "video_asset_id": str(UUID(int=1)), "position_seconds": 1, "played_seconds": 1},
    )  # fmt: skip
    assert response.status_code == 404
