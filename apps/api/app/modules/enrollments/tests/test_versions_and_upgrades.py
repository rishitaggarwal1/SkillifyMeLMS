"""How releases reach enrollments: minors apply to everyone automatically, majors only to new
enrollments until an org_admin opts their org (or chosen batches) in."""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.enrollments import service
from app.modules.enrollments.consumer import handle_event, handle_records
from tests.course_api import BuiltCourse, Campus, CourseApi, ok, published_for_cse


async def _detail(api: CourseApi, campus: Campus, student: Any, org: Any) -> dict[str, Any]:
    enrollment = (await api.enrollments(student, org))[0]
    return dict(ok(await api.request("GET", f"/enrollments/{enrollment['id']}", student, org)))


async def _complete(api: CourseApi, student: Any, org: Any, lesson: UUID) -> dict[str, Any]:
    enrollment = (await api.enrollments(student, org))[0]
    return dict(
        ok(
            await api.request(
                "POST", f"/enrollments/{enrollment['id']}/lessons/{lesson}/complete", student, org
            )
        )
    )


async def _retitle(api: CourseApi, campus: Campus, course: BuiltCourse, lesson: UUID) -> None:
    ok(
        await api.request(
            "PATCH", f"/courses/{course.id}/lessons/{lesson}", campus.author, campus.p,
            json={"title": "Fixed typo"},
        )
    )  # fmt: skip


async def test_minor_release_reaches_existing_enrollments_across_orgs(
    api: CourseApi, campus: Campus
) -> None:
    course = await published_for_cse(api, campus, modules=[("notes", "notes")])
    ok(await api.assign(campus.author, campus.p, course.id, batches=[campus.p_batch]), 201)
    await api.run_jobs()
    await _complete(api, campus.cse, campus.c, course.lesson_ids[0])

    await _retitle(api, campus, course, course.lesson_ids[1])
    ok(await api.publish(campus.author, campus.p, course.id, "minor"), 201)

    for student, org in ((campus.cse, campus.c), (campus.p_student, campus.p)):
        detail = await _detail(api, campus, student, org)
        titles = [x["title"] for x in detail["outline"]["modules"][0]["lessons"]]
        assert detail["version"]["version"] == "1.1"
        assert titles == ["notes lesson", "Fixed typo"]
    cse = await _detail(api, campus, campus.cse, campus.c)
    assert cse["enrollment"]["progress_percent"] == 50  # unchanged by the minor release


async def test_major_release_only_reaches_new_enrollments(api: CourseApi, campus: Campus) -> None:
    course = await published_for_cse(api, campus, modules=[("notes", "notes")])
    ok(
        await api.request(
            "POST", f"/courses/{course.id}/modules/{course.module_ids[0]}/lessons", campus.author,
            campus.p, json={"title": "New in 2.0", "lesson_type": "notes"},
        ),
        201,
    )  # fmt: skip
    ok(await api.publish(campus.author, campus.p, course.id, "major"), 201)
    ok(await api.assign(campus.c_admin, campus.c, course.id, batches=[campus.ece_batch]), 201)
    await api.run_jobs()

    cse = await _detail(api, campus, campus.cse, campus.c)
    ece = await _detail(api, campus, campus.ece, campus.c)
    assert (cse["version"]["version"], len(cse["outline"]["modules"][0]["lessons"])) == ("1.0", 2)
    assert (ece["version"]["version"], len(ece["outline"]["modules"][0]["lessons"])) == ("2.0", 3)


@dataclass
class UpgradeSetup:
    course: BuiltCourse
    kept: UUID
    dropped: UUID
    added: UUID


async def _two_majors(api: CourseApi, campus: Campus) -> UpgradeSetup:
    """1.0 = [kept, dropped]; CSE completes both. 2.0 = [kept, added]."""
    course = await published_for_cse(api, campus, modules=[("notes", "notes")])
    kept, dropped = course.lesson_ids
    await _complete(api, campus.cse, campus.c, kept)
    assert (await _complete(api, campus.cse, campus.c, dropped))["enrollment"]["completed_at"]

    ok(
        await api.request(
            "DELETE", f"/courses/{course.id}/lessons/{dropped}", campus.author, campus.p
        )
    )
    added = ok(
        await api.request(
            "POST", f"/courses/{course.id}/modules/{course.module_ids[0]}/lessons", campus.author,
            campus.p, json={"title": "Added", "lesson_type": "notes"},
        ),
        201,
    )["id"]  # fmt: skip
    ok(await api.publish(campus.author, campus.p, course.id, "major"), 201)
    return UpgradeSetup(course, kept, dropped, UUID(added))


async def test_org_admin_opts_in_and_progress_carries_over(
    api: CourseApi, campus: Campus, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    setup = await _two_majors(api, campus)
    course_id = setup.course.id

    accepted = ok(
        await api.request(
            "POST", f"/courses/{course_id}/enrollment-upgrades", campus.c_admin, campus.c,
            json={"to_major": 2, "batch_ids": [str(campus.cse_batch.id)]},
        ),
        202,
    )  # fmt: skip
    assert accepted["status"] == "queued"
    assert await api.run_jobs() == 1

    detail = await _detail(api, campus, campus.cse, campus.c)
    enrollment = detail["enrollment"]
    assert detail["version"]["version"] == "2.0"
    # `kept` carries over; `added` is new and required; `dropped` no longer counts.
    assert enrollment["progress_percent"] == 50
    assert enrollment["completed_at"] is None
    completed = {p["lesson_id"] for p in detail["progress"] if p["status"] == "completed"}
    assert completed == {str(setup.kept), str(setup.dropped)}  # history is kept

    async with owner_sessionmaker() as s:
        event: dict[str, Any] = (
            await s.execute(
                text(
                    "SELECT payload FROM outbox_events WHERE event_type = "
                    "'enrollment_version_changed' AND aggregate_id = :e"
                ),
                {"e": enrollment["id"]},
            )
        ).scalar_one()
    assert (event["from_major"], event["to_major"], event["progress_percent"]) == (1, 2, 50)
    assert event["actor_user_id"] == str(campus.c_admin.id)


async def test_only_the_students_org_admin_can_upgrade(
    api: CourseApi, campus: Campus, app: FastAPI
) -> None:
    setup = await _two_majors(api, campus)
    path = f"/courses/{setup.course.id}/enrollment-upgrades"
    body = {"to_major": 2}

    by_instructor = await api.request("POST", path, campus.c_instructor, campus.c, json=body)
    by_publisher_author = await api.request("POST", path, campus.author, campus.p, json=body)
    by_unrelated_admin = await api.request("POST", path, campus.o_admin, campus.o, json=body)
    unknown_version = await api.request(
        "POST", path, campus.c_admin, campus.c, json={"to_major": 3}
    )
    foreign_batch = await api.request(
        "POST", path, campus.c_admin, campus.c,
        json={"to_major": 2, "batch_ids": [str(campus.o_batch.id)]},
    )  # fmt: skip

    assert by_instructor.status_code == 403
    assert by_publisher_author.status_code == 403
    assert by_unrelated_admin.status_code == 404
    assert unknown_version.json()["error"]["code"] == "unknown_version"
    assert foreign_batch.json()["error"]["code"] == "invalid_batch"
    # Even run directly, the job acting as another org's admin moves nothing (RLS).
    moved = await service.run_upgrade(
        app.state.sessionmaker, course_id=setup.course.id, organization_id=campus.c.id,
        user_id=campus.p_admin.id, to_major=2, batch_ids=[],
    )  # fmt: skip
    assert moved == 0
    assert (await _detail(api, campus, campus.cse, campus.c))["version"]["version"] == "1.0"


async def test_upgrade_limited_to_chosen_batches(api: CourseApi, campus: Campus) -> None:
    setup = await _two_majors(api, campus)
    ok(await api.assign(campus.c_admin, campus.c, setup.course.id, batches=[campus.ece_batch]), 201)
    await api.run_jobs()
    # Move ECE's new 2.0 enrollment back to 1 to simulate an older ECE enrollment.
    ece_enrollment = (await api.enrollments(campus.ece, campus.c))[0]
    async with api.app.state.sessionmaker() as s, s.begin():
        await s.execute(text("SELECT set_config('app.platform_admin', 'true', true)"))
        await s.execute(
            text("UPDATE enrollments SET major_version = 1 WHERE id = :e"),
            {"e": ece_enrollment["id"]},
        )

    ok(
        await api.request(
            "POST", f"/courses/{setup.course.id}/enrollment-upgrades", campus.c_admin, campus.c,
            json={"to_major": 2, "batch_ids": [str(campus.ece_batch.id)]},
        ),
        202,
    )  # fmt: skip
    await api.run_jobs()

    assert (await _detail(api, campus, campus.ece, campus.c))["version"]["version"] == "2.0"
    assert (await _detail(api, campus, campus.cse, campus.c))["version"]["version"] == "1.0"


# ---------------------------------------------------------------------------- batch events


def _event(kind: str, campus: Campus, user_id: UUID, batch_id: UUID) -> dict[str, Any]:
    return {
        "id": str(UUID(int=1)),
        "type": kind,
        "version": 1,
        "data": {
            "batch_id": str(batch_id), "user_id": str(user_id),
            "organization_id": str(campus.c.id), "reason": "added",
        },
    }  # fmt: skip


async def test_batch_member_events_enroll_and_revoke(
    api: CourseApi,
    campus: Campus,
    app: FastAPI,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    course = await published_for_cse(api, campus, modules=[("notes",)])
    newcomer = await api.factory.member(campus.c, "student")
    await api.factory.add_to_batch(campus.cse_batch, newcomer)
    added = _event("batch_member_added", campus, newcomer.id, campus.cse_batch.id)

    assert await handle_event(app.state.sessionmaker, added) is True
    assert await handle_event(app.state.sessionmaker, added) is True  # redelivery: no-op
    enrolled = await api.enrollments(newcomer, campus.c)
    assert [e["course_id"] for e in enrolled] == [str(course.id)]

    async with owner_sessionmaker() as s, s.begin():
        await s.execute(
            text("DELETE FROM batch_members WHERE batch_id = :b AND user_id = :u"),
            {"b": campus.cse_batch.id, "u": newcomer.id},
        )
        created_events = await s.scalar(
            text(
                "SELECT count(*) FROM outbox_events WHERE event_type = 'enrollment_created' "
                "AND payload->>'user_id' = :u"
            ),
            {"u": str(newcomer.id)},
        )
    assert created_events == 1
    removed = _event("batch_member_removed", campus, newcomer.id, campus.cse_batch.id)
    assert await handle_event(app.state.sessionmaker, removed) is True
    assert await api.enrollments(newcomer, campus.c) == []


async def test_consumer_skips_foreign_and_malformed_records(app: FastAPI) -> None:
    @dataclass
    class Record:
        value: bytes | None
        offset: int

    records = [
        Record(b"not json", 1),
        Record(json.dumps({"type": "something_else", "data": {}}).encode(), 2),
        # Shaped like a membership event but invalid: skipped at once, never retried.
        Record(json.dumps({"type": "batch_member_added", "data": {"user_id": "u-1"}}).encode(), 3),
    ]
    assert await handle_records(app.state.sessionmaker, records, backoff_seconds=60) == 0
