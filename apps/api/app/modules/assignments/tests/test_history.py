"""Immutable work/grade history, frozen late/rubric rules and concurrency."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.assignments.tests.history_helpers import RUBRIC, AssignmentWorld, create_world
from tests.course_api import Campus, CourseApi, ok
from tests.fixtures import TenantSessionFactory


async def test_replacements_and_grade_corrections_keep_cursor_history(
    assignment_world: AssignmentWorld,
) -> None:
    w = assignment_world
    first = await w.submit(text="first")
    second = await w.submit(1, "second")
    assert first["id"] == second["id"]
    assert first["active_attempt_id"] != second["active_attempt_id"]
    assert second["attempt_number"] == 2
    assert second["revision"] == 2
    history = ok(await w.mine("GET", "submission-attempts", params={"limit": 1}))
    assert history["items"][0]["text_body"] == "second"
    assert history["next_cursor"]
    page = ok(
        await w.mine(
            "GET", "submission-attempts", params={"limit": 1, "cursor": history["next_cursor"]}
        )
    )
    assert page["items"][0]["text_body"] == "first"
    assert not page["items"][0]["is_active"]
    assert page["next_cursor"] is None
    inactive = await w.grade(second["id"], 2, score="8", attempt_id=first["active_attempt_id"])
    assert inactive.status_code == 409
    assert inactive.json()["error"]["code"] == "inactive_attempt"
    initial = ok(await w.grade(second["id"], 2, score="8"))["submission"]
    corrected = ok(await w.grade(second["id"], 3, score="9"))["submission"]
    assert initial["grade"]["id"] != corrected["grade"]["id"]
    assert corrected["grade"]["grade_sequence"] == 2
    prefix = f"submission-attempts/{second['active_attempt_id']}/grades"
    history = ok(await w.mine("GET", prefix, params={"limit": 1}))
    assert history["items"][0]["score"] == "9.00"
    assert history["next_cursor"]
    old = ok(await w.mine("GET", prefix, params={"limit": 1, "cursor": history["next_cursor"]}))
    assert old["items"][0]["score"] == "8.00"
    assert old["next_cursor"] is None
    staff = ok(
        await w.api.request(
            "GET",
            f"/assignment-submissions/{second['id']}/attempts",
            w.campus.c_instructor,
            w.campus.c,
        )
    )
    assert [a["text_body"] for a in staff["items"]] == ["second", "first"]
    async with w.api.factory.sessionmaker() as reader:
        params = {"eid": UUID(w.enrollment_id)}
        assert (
            await reader.scalar(
                text("SELECT progress_percent FROM enrollments WHERE id=:eid"), params
            )
            == 100
        )
        assert (
            await reader.scalar(
                text(
                    "SELECT count(*) FROM outbox_events WHERE aggregate_id=:eid AND "
                    "event_type='lesson_completed'"
                ),
                params,
            )
            == 1
        )
        assert (
            await reader.scalar(
                text("SELECT count(*) FROM assignment_grades WHERE submission_id=:sid"),
                {"sid": UUID(second["id"])},
            )
            == 2
        )


async def test_frozen_rubric_and_earned_late_penalty(api: CourseApi, campus: Campus) -> None:
    due = datetime.now(UTC) - timedelta(hours=1)
    w = await create_world(
        api,
        campus,
        rubric=RUBRIC,
        due_at=due.isoformat(),
        late_policy={"mode": "penalty", "percent_per_day": "10"},
    )
    before = ok(await w.mine("GET", "assignment"))
    assert before["server_time"]
    assert before["late"]["late_days"] == 1
    submitted = await w.submit()
    assert submitted["late"]["penalty_percent"] == "10"
    missing = await w.grade(submitted["id"], 1, score="10")
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "rubric_scores_required"
    ok(
        await api.define_assignment(
            campus.author,
            campus.p,
            w.course.id,
            w.lesson_id,
            due_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            late_policy={"mode": "reject"},
            rubric={"criteria": [{**c, "label": "Edited"} for c in RUBRIC["criteria"]]},
        )
    )
    ok(await api.publish(campus.author, campus.p, w.course.id, "minor"), 201)
    scores = [{"criterion_id": "correct", "score": "5"}, {"criterion_id": "clear", "score": "3"}]
    graded = ok(await w.grade(submitted["id"], 1, score="10", criterion_scores=scores))[
        "submission"
    ]["grade"]
    assert (graded["raw_score"], graded["penalty_marks"], graded["score"]) == (
        "8.00",
        "0.80",
        "7.20",
    )
    changed = ok(await w.grade(submitted["id"], 2, criterion_scores=scores))["submission"]["grade"]
    assert changed["score"] == "7.20"
    assert changed["grade_sequence"] == 2
    history = ok(await w.mine("GET", "submission-attempts"))["items"][0]
    assert history["assignment"]["rubric"] == {
        "criteria": [
            {**c, "max_marks": c["max_marks"], "description": ""} for c in RUBRIC["criteria"]
        ]
    }
    assert history["late"]["policy"]["mode"] == "penalty"


async def test_reject_late_has_no_work_or_event(api: CourseApi, campus: Campus) -> None:
    w = await create_world(
        api,
        campus,
        due_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        late_policy={"mode": "reject"},
    )
    before = ok(await w.mine("GET", "assignment"))
    assert before["late"]["closed"]
    for suffix, method, body in [
        ("submission", "PUT", {"submission": {"kind": "text", "text": "late"}}),
        ("submission-upload", "POST", {"file_name": "a.pdf", "content_type": "application/pdf"}),
    ]:
        response = await w.mine(method, suffix, json=body, headers={"If-Match": "0"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "assignment_closed"
    async with api.factory.sessionmaker() as reader:
        params = {"eid": UUID(w.enrollment_id)}
        assert (
            await reader.scalar(
                text("SELECT count(*) FROM assignment_submissions WHERE enrollment_id=:eid"), params
            )
            == 0
        )
        assert (
            await reader.scalar(
                text(
                    "SELECT count(*) FROM outbox_events WHERE aggregate_id=:eid AND "
                    "event_type='assignment_submitted'"
                ),
                params,
            )
            == 0
        )


async def test_submit_grade_race_preserves_active_attempt(
    assignment_world: AssignmentWorld,
) -> None:
    w = assignment_world
    sub = await w.submit()
    responses = await asyncio.gather(
        w.mine(
            "PUT",
            "submission",
            headers={"If-Match": "1"},
            json={"submission": {"kind": "text", "text": "new"}},
        ),
        w.grade(sub["id"], 1, score="7"),
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    async with w.api.factory.sessionmaker() as reader:
        row = (
            await reader.execute(
                text(
                    "SELECT s.status,s.text_body,a.text_body AS "
                    "attempt_body,s.revision FROM assignment_submissions s JOIN "
                    "submission_attempts a ON a.id=s.active_attempt_id WHERE s.id=:id"
                ),
                {"id": UUID(sub["id"])},
            )
        ).one()
        assert row.text_body == row.attempt_body
        assert row.revision == 2
        grades = await reader.scalar(
            text("SELECT count(*) FROM assignment_grades WHERE submission_id=:id"),
            {"id": UUID(sub["id"])},
        )
        assert grades == (1 if row.status == "graded" else 0)


async def test_history_rls_and_immutable_grants(
    assignment_world: AssignmentWorld, tenant_session: TenantSessionFactory
) -> None:
    w = assignment_world
    sub = await w.submit()
    await w.submit(1, "replacement")
    ok(await w.grade(sub["id"], 2, score="8"))
    params = {"sid": UUID(sub["id"])}
    sql = (
        "SELECT a.id,g.score FROM submission_attempts a LEFT JOIN "
        "assignment_grades g ON g.attempt_id=a.id WHERE "
        "a.submission_id=:sid"
    )
    for user, org, visible in [
        (w.campus.cse, w.campus.c, True),
        (w.campus.c_instructor, w.campus.c, True),
        (w.campus.ece, w.campus.c, False),
        (w.campus.author, w.campus.p, False),
        (w.campus.o_admin, w.campus.o, False),
    ]:
        async with tenant_session(org=org.id, user=user.id) as session:
            assert len((await session.execute(text(sql), params)).all()) == (2 if visible else 0)
            assert (
                await session.scalar(
                    text("SELECT app.lock_assessment_enrollment(:eid)"),
                    {"eid": UUID(w.enrollment_id)},
                )
                is visible
            )
    for table in ["submission_attempts", "assignment_grades"]:
        for action in ["UPDATE", "DELETE"]:
            async with tenant_session(org=w.campus.c.id, user=w.campus.c_instructor.id) as session:
                with pytest.raises(DBAPIError, match="permission denied"):
                    # Static choices above, never client SQL.
                    await session.execute(
                        text(
                            f"{action} "
                            + ("FROM " if action == "DELETE" else "")
                            + table
                            + (" SET updated_at=now()" if action == "UPDATE" else "")
                            + " WHERE submission_id=:sid"
                        ),
                        params,
                    )


@pytest.mark.parametrize(
    "fields",
    [
        {"rubric": {"criteria": [{"id": "a", "label": "A", "max_marks": "9"}]}},
        {
            "rubric": {
                "criteria": [
                    {"id": "a", "label": "A", "max_marks": "5"},
                    {"id": "a", "label": "B", "max_marks": "5"},
                ]
            }
        },
        {"late_policy": {"mode": "penalty", "percent_per_day": "0"}},
        {"late_policy": {"mode": "penalty", "percent_per_day": "10"}, "due_at": None},
    ],
)
async def test_definition_rule_validation(
    assignment_world: AssignmentWorld, fields: dict[str, Any]
) -> None:
    w = assignment_world
    response = await w.api.define_assignment(
        w.campus.author, w.campus.p, w.course.id, w.lesson_id, **fields
    )
    assert response.status_code == 422


async def test_legacy_definition_save_preserves_phase3_fields(
    api: CourseApi, campus: Campus
) -> None:
    w = await create_world(api, campus, rubric=RUBRIC, late_policy={"mode": "accept"})
    saved = ok(
        await api.define_assignment(
            campus.author, campus.p, w.course.id, w.lesson_id, title="Legacy edit"
        )
    )
    assert saved["rubric"] is not None
    assert saved["late_policy"]["mode"] == "accept"
    clear = ok(
        await api.define_assignment(
            campus.author, campus.p, w.course.id, w.lesson_id, rubric=None, late_policy=None
        )
    )
    assert clear["rubric"] is None
    assert clear["late_policy"] is None


async def test_raw_insert_guards_reject_forged_rules_and_projection(
    assignment_world: AssignmentWorld, tenant_session: TenantSessionFactory
) -> None:
    w = assignment_world
    sub = await w.submit()
    image = await w.api.factory.image(w.campus.o)
    values = {"id": UUID(sub["active_attempt_id"]), "image": str(image.id)}
    async with tenant_session(org=w.campus.c.id, user=w.campus.cse.id) as session:
        with pytest.raises(DBAPIError, match="published assignment"):
            await session.execute(
                text(
                    "INSERT INTO submission_attempts (id,submission_id,organization_id,user_id,"
                    "attempt_number,major_version,version_id,kind,text_body,file_id,submitted_at,"
                    "assignment_rules,late_data) SELECT gen_random_uuid(),submission_id,"
                    "organization_id,user_id,2,major_version,version_id,kind,text_body,file_id,"
                    "submitted_at,jsonb_set(assignment_rules,'{image_file_ids}',"
                    "jsonb_build_array(CAST(:image AS text))),late_data "
                    "FROM submission_attempts WHERE id=:id"
                ),
                values,
            )
    async with tenant_session(org=w.campus.c.id, user=w.campus.c_instructor.id) as session:
        with pytest.raises(DBAPIError, match="frozen rules"):
            await session.execute(
                text(
                    "INSERT INTO assignment_grades (id,organization_id,submission_id,user_id,"
                    "attempt_id,grade_sequence,score,raw_score,max_marks,"
                    "penalty_percent,penalty_marks) "
                    "SELECT gen_random_uuid(),organization_id,submission_id,user_id,id,1,7.2,8,10,"
                    "10,0.8 FROM submission_attempts WHERE id=:id"
                ),
                values,
            )
    async with tenant_session(org=w.campus.c.id, user=w.campus.cse.id) as session:
        await session.execute(
            text("UPDATE assignment_submissions SET text_body='forged' WHERE id=:sid"),
            {"sid": UUID(sub["id"])},
        )
        with pytest.raises(DBAPIError, match="active immutable attempt"):
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    async with w.api.factory.sessionmaker() as reader:
        assert (
            await reader.scalar(
                text("SELECT count(*) FROM submission_attempts WHERE submission_id=:sid"),
                {"sid": UUID(sub["id"])},
            )
            == 1
        )
        assert (
            await reader.scalar(
                text("SELECT count(*) FROM assignment_grades WHERE submission_id=:sid"),
                {"sid": UUID(sub["id"])},
            )
            == 0
        )
        assert (
            await reader.scalar(
                text("SELECT text_body FROM assignment_submissions WHERE id=:sid"),
                {"sid": UUID(sub["id"])},
            )
            == "work"
        )
