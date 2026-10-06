"""docs/events.md is the contract: every event the app writes validates against its documented
JSON Schema, goes to its documented topic, and every documented event is exercised here."""

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from app.db.base import new_id
from app.db.outbox import OutboxEvent
from app.events.envelope import DEFAULT_TOPIC, TOPICS, topic_for
from app.modules.assessments.tests.authoring_helpers import build_quiz
from app.modules.assessments.tests.runtime_helpers import correct, publish_for_student
from app.modules.enrollments.video_flush import flush_video_progress
from tests.course_api import Campus, CourseApi, ok, published_for_cse

DOC = Path(__file__).resolve().parents[3] / "docs" / "events.md"


def documented_schemas() -> dict[tuple[str, int], dict[str, object]]:
    text = DOC.read_text(encoding="utf-8")
    found = re.findall(r"### `(\w+)` \(version (\d+)\)\s.*?```json\n(.*?)\n```", text, re.S)
    schemas = {(name, int(version)): json.loads(body) for name, version, body in found}
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    return schemas


def documented_topics() -> tuple[dict[str, str], dict[str, str]]:
    """(aggregate type -> topic) from the Topics table, (event type -> topic) from the catalogue."""
    text = DOC.read_text(encoding="utf-8")
    # Topics table rows have two columns; catalogue rows have a third (when it's emitted).
    by_aggregate = dict(
        re.findall(r"^\|[ \t]*`(\w+)`[ \t]*\|[ \t]*`([\w.-]+)`[ \t]*\|$", text, re.M)
    )
    by_event = dict(
        re.findall(r"^\|[ \t]*`(\w+)`[ \t]*\|[ \t]*`([\w.-]+)`[ \t]*\|[^|\n]+\|$", text, re.M)
    )
    return by_aggregate, by_event


def test_topic_table_matches_the_code() -> None:
    by_aggregate, _ = documented_topics()
    assert by_aggregate == TOPICS
    assert f"`{DEFAULT_TOPIC}`" in DOC.read_text(encoding="utf-8")


async def test_every_event_validates_against_its_documented_schema(
    api: CourseApi, campus: Campus
) -> None:
    schemas = documented_schemas()
    _, event_topics = documented_topics()

    # Publishing and assigning emit course and enrollment events; batch changes, member events.
    course = await published_for_cse(api, campus, modules=(("video", "notes", "assignment"),))
    enrollment = await api.enrollment_for(campus.cse, campus.c, course.id)
    assert enrollment
    eid, (video, notes, homework) = enrollment["id"], course.lesson_ids
    newcomer = await api.factory.member(campus.c, "student")
    batch = f"/batches/{campus.cse_batch.id}/members"
    ok(await api.request("POST", batch, campus.c_admin, campus.c,
                         json={"user_ids": [str(newcomer.id)]}))  # fmt: skip
    ok(await api.request("DELETE", f"{batch}/{newcomer.id}", campus.c_admin, campus.c), 204)
    # Completing notes, then watching and flushing video, emit the progress events.
    ok(await api.request("POST", f"/enrollments/{eid}/lessons/{notes}/complete", campus.cse,
                         campus.c))  # fmt: skip
    asset = ok(await api.request("GET", f"/enrollments/{eid}/lessons/{video}/resume", campus.cse,
                                 campus.c))["video_asset_id"]  # fmt: skip
    response = await api.request(
        "POST", "/progress/heartbeat", campus.cse, campus.c,
        json={"enrollment_id": eid, "lesson_id": str(video), "video_asset_id": asset,
              "position_seconds": 15, "played_seconds": 15},
    )  # fmt: skip
    assert response.status_code == 204
    await flush_video_progress(api.app.state.sessionmaker, api.app.state.redis)
    # Submitting and grading an assignment emit the assignment events (and lesson_completed).
    submission = ok(
        await api.request(
            "PUT", f"/enrollments/{eid}/lessons/{homework}/submission", campus.cse, campus.c,
            json={"submission": {"kind": "text", "text": "print(1)"}}, headers={"If-Match": "0"},
        )
    )  # fmt: skip
    ok(
        await api.request(
            "PUT", f"/assignment-submissions/{submission['id']}/grade", campus.c_instructor,
            campus.c, json={"score": "7.5", "feedback": "ok"}, headers={"If-Match": "1"},
        )
    )  # fmt: skip
    # A real passing quiz emits the new strict v1 contract on the registered enrollment topic.
    quiz = await build_quiz(api, campus)
    player = await publish_for_student(quiz)
    attempt = await player.start()
    result = ok(
        await player.request(
            "POST",
            f"/quiz-attempts/{attempt['id']}/submit",
            json=correct(attempt),
            headers={"If-Match": "1"},
        )
    )
    assert result["passed"] is True
    # Opting into a new major emits the version change.
    ok(await api.publish(campus.author, campus.p, course.id, "major"), 201)
    ok(await api.request("POST", f"/courses/{course.id}/enrollment-upgrades", campus.c_admin,
                         campus.c, json={"to_major": 2}), 202)  # fmt: skip
    await api.run_jobs()

    async with api.factory.sessionmaker() as session:
        events = list(
            await session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.organization_id.in_([campus.p.id, campus.c.id])
                )
            )
        )
    # Historical v1 contracts stay exercised independently of the current producer.
    current = next(e for e in events if e.event_type == "assignment_graded")
    old_keys = schemas[("assignment_graded", 1)]["properties"]
    assert isinstance(old_keys, dict)
    legacy = OutboxEvent(
        id=new_id(),
        organization_id=current.organization_id,
        aggregate_type=current.aggregate_type,
        aggregate_id=current.aggregate_id,
        event_type="assignment_graded",
        headers={"version": 1},
        payload={k: current.payload[k] for k in old_keys},
    )
    await api.factory._save(legacy)
    events.append(legacy)
    seen: set[tuple[str, int]] = set()
    checker = FormatChecker()
    for outbox in events:
        version = outbox.headers.get("version")
        assert isinstance(version, int)
        key = (outbox.event_type, version)
        schema = schemas.get(key)
        assert schema is not None, f"{outbox.event_type} has no schema in docs/events.md"
        errors = list(
            Draft202012Validator(schema, format_checker=checker).iter_errors(outbox.payload)
        )
        assert errors == [], (outbox.event_type, [e.message for e in errors])
        assert topic_for(outbox) == event_topics[outbox.event_type], outbox.event_type
        assert version == (
            2 if outbox.event_type == "assignment_graded" and outbox.id != legacy.id else 1
        )
        seen.add(key)
    assert seen == set(schemas), f"documented but not produced: {set(schemas) - seen}"
