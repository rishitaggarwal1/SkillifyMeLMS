"""Quiz execution, immutable rules, independent reveal gates and durable progress."""

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import create_sessionmaker
from app.modules.assessments.tasks import run_expiry, sweep
from app.modules.assessments.tests.authoring_helpers import AuthoredQuiz
from app.modules.assessments.tests.runtime_helpers import RuntimeWorld, correct, publish_for_student
from tests.course_api import ok
from tests.fixtures import TenantSessionFactory


async def test_passing_expiry_sweeper_without_client_survives_restart(
    runtime: RuntimeWorld,
) -> None:
    w = runtime
    attempt = await w.start()
    ok(
        await w.request(
            "PUT",
            f"/quiz-attempts/{attempt['id']}/answers",
            json=correct(attempt),
            headers={"If-Match": "1"},
        )
    )
    await w.expire(attempt["id"])
    # No client request after expiry: invoke the production beat handler with
    # fresh app-role connections, then recreate them to simulate worker restart.
    settings = w.authored.api.app.state.settings
    first_state = None
    for iteration in range(2):
        engine = create_async_engine(settings.database_url.get_secret_value(), poolclass=NullPool)
        try:
            sm = create_sessionmaker(engine)
            count = await sweep(sm)
            if iteration == 0:
                assert count >= 1
            # A queued expiry task may also be replayed after the beat worker.
            await run_expiry(sm, UUID(attempt["id"]), w.authored.campus.c.id)
        finally:
            await engine.dispose()
        async with w.authored.api.factory.sessionmaker() as reader:
            state = (
                await reader.execute(
                    text(
                        "SELECT state, score, passed, revision, submitted_at "
                        "FROM quiz_attempts WHERE id=:id"
                    ),
                    {"id": UUID(attempt["id"])},
                )
            ).one()
            assert tuple(state[:4]) == ("submitted", 6, True, 3)
            assert state.submitted_at is not None
            if first_state is None:
                first_state = state
            else:
                assert state == first_state
            params = {"id": UUID(w.enrollment_id), "aid": attempt["id"]}
            assert (
                await reader.scalar(
                    text("SELECT progress_percent FROM enrollments WHERE id=:id"), params
                )
                == 100
            )
            assert (
                await reader.scalar(
                    text(
                        "SELECT count(*) FROM lesson_progress "
                        "WHERE enrollment_id=:id AND completed_at IS NOT NULL"
                    ),
                    params,
                )
                == 1
            )
            assert (
                await reader.scalar(
                    text(
                        "SELECT count(*) FROM outbox_events WHERE aggregate_id=:id "
                        "AND event_type='lesson_completed'"
                    ),
                    params,
                )
                == 1
            )
            assert (
                await reader.scalar(
                    text(
                        "SELECT count(*) FROM outbox_events WHERE aggregate_id=:id "
                        "AND event_type='quiz_attempt_submitted' "
                        "AND payload->>'attempt_id'=:aid AND payload->>'reason'='expiry'"
                    ),
                    params,
                )
                == 1
            )


async def test_fail_then_pass_and_idempotent_submission(runtime: RuntimeWorld) -> None:
    w = runtime
    rules = ok(await w.request("GET", w.path + "/quiz"))
    assert (rules["revision"], rules["attempts_remaining"]) == (0, 2)
    first = await w.start()
    assert len(first["questions"]) == 3
    assert first["revision"] == 1
    assert "answer_key" not in json.dumps(first)
    assert "PRIVATE" not in json.dumps(first)
    failed = ok(
        await w.request(
            "POST",
            f"/quiz-attempts/{first['id']}/submit",
            json={"answers": []},
            headers={"If-Match": "1"},
        )
    )
    assert failed["score"] == "0.00"
    assert failed["passed"] is False
    second = await w.start(1)
    passed = ok(
        await w.request(
            "POST",
            f"/quiz-attempts/{second['id']}/submit",
            json=correct(second),
            headers={"If-Match": "1"},
        )
    )
    assert passed["score"] == "6.00"
    assert passed["passed"] is True
    assert passed["reveal_mode"] == "score_only"
    assert "solutions" not in passed
    again = ok(
        await w.request(
            "POST", f"/quiz-attempts/{second['id']}/submit", json={}, headers={"If-Match": "1"}
        )
    )
    assert again == passed
    a = w.authored
    e = ok(await w.request("GET", f"/enrollments/{w.enrollment_id}"))
    assert e["enrollment"]["progress_percent"] == 100
    async with a.api.factory.sessionmaker() as s:
        assert (
            await s.scalar(
                text(
                    "SELECT count(*) FROM outbox_events WHERE aggregate_id=:id "
                    "AND event_type='quiz_attempt_submitted'"
                ),
                {"id": UUID(w.enrollment_id)},
            )
            == 2
        )
        assert (
            await s.scalar(
                text(
                    "SELECT count(*) FROM outbox_events WHERE aggregate_id=:id "
                    "AND event_type='lesson_completed'"
                ),
                {"id": UUID(w.enrollment_id)},
            )
            == 1
        )
    quota = await w.request("POST", w.path + "/quiz-attempts", headers={"If-Match": "2"})
    assert quota.status_code == 409
    assert quota.json()["error"]["code"] == "attempt_limit"
    manual = await w.request("POST", w.path + "/complete")
    assert manual.status_code == 409
    assert manual.json()["error"]["code"] == "completed_by_quiz"
    history = ok(await w.request("GET", w.path + "/quiz-attempts", params={"limit": 1}))
    assert len(history["items"]) == 1
    assert history["next_cursor"]
    page2 = ok(
        await w.request(
            "GET", w.path + "/quiz-attempts", params={"limit": 1, "cursor": history["next_cursor"]}
        )
    )
    assert len(page2["items"]) == 1
    assert page2["items"][0]["id"] != history["items"][0]["id"]


@pytest.mark.parametrize(
    ("options", "score"),
    [
        (["a"], "5.00"),
        (["a", "b"], "6.00"),
        (["a", "c"], "4.00"),
        (["a", "b", "c"], "4.00"),
        ([], "4.00"),
    ],
)
async def test_multi_partial_marks(runtime: RuntimeWorld, options: list[str], score: str) -> None:
    a = await runtime.start()
    result = ok(
        await runtime.request(
            "POST",
            f"/quiz-attempts/{a['id']}/submit",
            json=correct(a, options),
            headers={"If-Match": "1"},
        )
    )
    assert result["score"] == score


@pytest.mark.parametrize(
    "bad",
    [
        {"option_ids": ["a", "a"]},
        {"option_ids": ["unknown"]},
        {"option_ids": ["a", "b"]},
        {"text": "a"},
        {"option_ids": ["a"], "score": 100},
    ],
)
async def test_bad_batch_changes_nothing(runtime: RuntimeWorld, bad: dict[str, Any]) -> None:
    a = await runtime.start()
    response = await runtime.request(
        "PUT",
        f"/quiz-attempts/{a['id']}/answers",
        json={"answers": [{"question_id": a["questions"][0]["id"], "answer": bad}]},
        headers={"If-Match": "1"},
    )
    assert response.status_code == 422
    resumed = ok(await runtime.request("GET", f"/quiz-attempts/{a['id']}"))
    assert resumed["revision"] == 1
    assert all(q["saved_answer"] is None for q in resumed["questions"])


async def test_save_resume_and_server_timer(runtime: RuntimeWorld) -> None:
    a = await runtime.start()
    path = f"/quiz-attempts/{a['id']}"
    premature = await runtime.request("GET", path + "/results")
    assert premature.status_code == 409
    saved = ok(
        await runtime.request("PUT", path + "/answers", json=correct(a), headers={"If-Match": "1"})
    )
    resumed = ok(await runtime.request("GET", path))
    assert saved["revision"] == resumed["revision"] == 2
    assert resumed["expires_at"] == a["expires_at"]
    assert resumed["started_at"] == a["started_at"]
    assert all(q["saved_answer"] is not None for q in resumed["questions"])
    active = await runtime.request(
        "POST", runtime.path + "/quiz-attempts", headers={"If-Match": "1"}
    )
    assert active.status_code == 409
    assert active.json()["error"]["code"] == "attempt_active"
    await runtime.expire(a["id"])
    for revision in (2, 2):
        late = await runtime.request(
            "PUT", path + "/answers", json=correct(a), headers={"If-Match": str(revision)}
        )
        assert late.status_code == 409
        assert late.json()["error"]["code"] == "attempt_expired"
    result = ok(
        await runtime.request(
            "POST", path + "/submit", json={"answers": []}, headers={"If-Match": "2"}
        )
    )
    assert result["score"] == "6.00"
    async with runtime.authored.api.factory.sessionmaker() as s:
        event = await s.scalar(
            text("SELECT payload FROM outbox_events WHERE payload->>'attempt_id'=:id"),
            {"id": a["id"]},
        )
        assert event["reason"] == "expiry"


async def test_late_submit_ignores_new_answers_and_sweeper_recovers(runtime: RuntimeWorld) -> None:
    a = await runtime.start()
    await runtime.expire(a["id"])
    result = ok(
        await runtime.request(
            "POST", f"/quiz-attempts/{a['id']}/submit", json=correct(a), headers={"If-Match": "1"}
        )
    )
    assert result["score"] == "0.00"
    b = await runtime.start(1)
    await runtime.expire(b["id"])
    sm = runtime.authored.api.app.state.sessionmaker
    assert await sweep(sm) >= 1
    await sweep(sm)
    await run_expiry(sm, UUID(b["id"]), runtime.authored.campus.c.id)
    result = ok(await runtime.request("GET", f"/quiz-attempts/{b['id']}/results"))
    assert result["score"] == "0.00"
    async with runtime.authored.api.factory.sessionmaker() as s:
        assert (
            await s.scalar(
                text("SELECT count(*) FROM outbox_events WHERE payload->>'attempt_id'=:id"),
                {"id": b["id"]},
            )
            == 1
        )


@pytest.mark.parametrize("mode", ["score_only", "correct_answers", "explanations"])
@pytest.mark.parametrize("timing", ["immediately", "after_attempts_exhausted"])
async def test_reveal_both_layers(
    authored: AuthoredQuiz, mode: str, timing: str, tenant_session: TenantSessionFactory
) -> None:
    ok(await authored.put(reveal_mode=mode, reveal_timing=timing))
    w = await publish_for_student(authored)
    a = await w.start()

    async def sql_solutions() -> list[Any]:
        async with tenant_session(org=authored.campus.c.id, user=authored.campus.cse.id) as s:
            return list(
                (
                    await s.execute(
                        text("SELECT * FROM app.quiz_attempt_solutions(:id)"), {"id": UUID(a["id"])}
                    )
                ).all()
            )

    assert await sql_solutions() == []
    result = ok(
        await w.request(
            "POST", f"/quiz-attempts/{a['id']}/submit", json=correct(a), headers={"If-Match": "1"}
        )
    )
    allowed = mode != "score_only" and timing == "immediately"
    assert bool(await sql_solutions()) == allowed
    assert ("solutions" in result) == allowed
    if allowed:
        assert ("explanation" in result["solutions"][0]) == (mode == "explanations")
    b = await w.start(1)
    if timing == "after_attempts_exhausted":
        assert await sql_solutions() == []
    ok(
        await w.request(
            "POST", f"/quiz-attempts/{b['id']}/submit", json={}, headers={"If-Match": "1"}
        )
    )
    result = ok(await w.request("GET", f"/quiz-attempts/{a['id']}/results"))
    assert ("solutions" in result) == (mode != "score_only")
    assert bool(await sql_solutions()) == (mode != "score_only")


async def test_concurrent_start_and_save(runtime: RuntimeWorld) -> None:
    responses = await asyncio.gather(
        *(
            runtime.request("POST", runtime.path + "/quiz-attempts", headers={"If-Match": "0"})
            for _ in range(2)
        )
    )
    assert sorted(r.status_code for r in responses) == [201, 409]
    a = next(r.json() for r in responses if r.status_code == 201)
    responses = await asyncio.gather(
        *(
            runtime.request(
                "PUT",
                f"/quiz-attempts/{a['id']}/answers",
                json=correct(a),
                headers={"If-Match": "1"},
            )
            for _ in range(2)
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 409]


async def test_minor_freezes_active_rules_major_closes_and_resets(runtime: RuntimeWorld) -> None:
    w, a = runtime, runtime.authored
    attempt = await w.start()
    ok(await a.put(reveal_mode="explanations"))
    ok(await a.publish("minor"), 201)
    result = ok(
        await w.request(
            "POST",
            f"/quiz-attempts/{attempt['id']}/submit",
            json=correct(attempt),
            headers={"If-Match": "1"},
        )
    )
    assert result["reveal_mode"] == "score_only"
    active = await w.start(1)
    ok(await a.publish("major"), 201)
    ok(
        await a.api.request(
            "POST",
            f"/courses/{a.course.id}/enrollment-upgrades",
            a.campus.c_admin,
            a.campus.c,
            json={"to_major": 2},
        ),
        202,
    )
    await a.api.run_jobs()
    rules = ok(await w.request("GET", w.path + "/quiz"))
    assert rules["revision"] == 0
    assert rules["attempts_remaining"] == 2
    assert (await w.request("GET", f"/quiz-attempts/{active['id']}")).status_code == 404
    async with a.api.factory.sessionmaker() as s:
        assert (
            await s.scalar(
                text("SELECT state FROM quiz_attempts WHERE id=:id"), {"id": UUID(active["id"])}
            )
            == "abandoned"
        )
    e = ok(await w.request("GET", f"/enrollments/{w.enrollment_id}"))
    assert e["enrollment"]["progress_percent"] == 100  # stable lesson completion carries forward


async def _revoke_and_seal(runtime: RuntimeWorld) -> None:
    w, a = runtime, runtime.authored
    attempt = await w.start()
    ok(
        await w.request(
            "PUT",
            f"/quiz-attempts/{attempt['id']}/answers",
            json=correct(attempt),
            headers={"If-Match": "1"},
        )
    )
    assignments = ok(
        await a.api.request(
            "GET", f"/courses/{a.course.id}/assignments", a.campus.author, a.campus.p
        )
    )["items"]
    row = next(r for r in assignments if r.get("batch_id") == str(a.campus.cse_batch.id))
    ok(
        await a.api.request(
            "DELETE",
            f"/course-assignments/{row['id']}",
            a.campus.c_admin,
            a.campus.c,
        ),
        204,
    )
    assert (await w.request("GET", f"/quiz-attempts/{attempt['id']}")).status_code == 404
    await w.expire(attempt["id"])
    await sweep(a.api.app.state.sessionmaker)
    async with a.api.factory.sessionmaker() as s:
        assert (
            await s.scalar(
                text("SELECT state FROM quiz_attempts WHERE id=:id"), {"id": UUID(attempt["id"])}
            )
            == "submitted"
        )
        assert (
            await s.scalar(
                text("SELECT progress_percent FROM enrollments WHERE id=:id"),
                {"id": UUID(w.enrollment_id)},
            )
            == 0
        )


async def test_revocation_hides_runtime_and_expiry_does_not_complete(runtime: RuntimeWorld) -> None:
    await _revoke_and_seal(runtime)


async def test_revoked_old_major_pass_does_not_create_new_major_completion(
    runtime: RuntimeWorld,
) -> None:
    await _revoke_and_seal(runtime)
    w, a = runtime, runtime.authored
    ok(
        await a.api.assign(a.campus.c_admin, a.campus.c, a.course.id, batches=[a.campus.cse_batch]),
        201,
    )
    await a.api.run_jobs()
    ok(await a.publish("major"), 201)
    ok(
        await a.api.request(
            "POST",
            f"/courses/{a.course.id}/enrollment-upgrades",
            a.campus.c_admin,
            a.campus.c,
            json={"to_major": 2},
        ),
        202,
    )
    await a.api.run_jobs()
    enrollment = ok(await w.request("GET", f"/enrollments/{w.enrollment_id}"))
    assert enrollment["enrollment"]["major_version"] == 2
    assert enrollment["enrollment"]["progress_percent"] == 0
    assert not any(p["status"] == "completed" for p in enrollment["progress"])
    rules = ok(await w.request("GET", w.path + "/quiz"))
    assert rules["attempts_used"] == 0


async def test_early_expiry_job_reschedules_without_finalizing(runtime: RuntimeWorld) -> None:
    from datetime import datetime  # noqa: PLC0415

    w = runtime
    attempt = await w.start()
    deadline = await run_expiry(
        w.authored.api.app.state.sessionmaker, UUID(attempt["id"]), w.authored.campus.c.id
    )
    assert deadline == datetime.fromisoformat(attempt["expires_at"])
    state = ok(await w.request("GET", f"/quiz-attempts/{attempt['id']}"))
    assert state["state"] == "in_progress"
    assert state["revision"] == 1


async def test_sweeper_skips_locked_work_and_retries_later(runtime: RuntimeWorld) -> None:
    w = runtime
    attempt = await w.start()
    await w.expire(attempt["id"])
    async with w.authored.api.factory.sessionmaker() as blocker, blocker.begin():
        await blocker.execute(
            text("SELECT id FROM enrollments WHERE id=:id FOR UPDATE"),
            {"id": UUID(w.enrollment_id)},
        )
        await asyncio.wait_for(sweep(w.authored.api.app.state.sessionmaker), 5)
        assert (
            await blocker.scalar(
                text("SELECT state FROM quiz_attempts WHERE id=:id"), {"id": UUID(attempt["id"])}
            )
            == "in_progress"
        )
    await sweep(w.authored.api.app.state.sessionmaker)
    result = ok(await w.request("GET", f"/quiz-attempts/{attempt['id']}/results"))
    assert result["score"] == "0.00"


async def test_quiz_and_assignment_finish_concurrently(authored: AuthoredQuiz) -> None:
    a = authored
    lesson = ok(
        await a.api.request(
            "POST",
            f"/courses/{a.course.id}/modules/{a.course.module_ids[0]}/lessons",
            a.campus.author,
            a.campus.p,
            json={"title": "Homework", "lesson_type": "assignment"},
        ),
        201,
    )
    lid = UUID(lesson["id"])
    ok(await a.api.define_assignment(a.campus.author, a.campus.p, a.course.id, lid))
    w = await publish_for_student(a)
    attempt = await w.start()
    submission = ok(
        await w.request(
            "PUT",
            f"/enrollments/{w.enrollment_id}/lessons/{lid}/submission",
            json={"submission": {"kind": "text", "text": "Solution"}},
            headers={"If-Match": "0"},
        )
    )
    responses = await asyncio.wait_for(
        asyncio.gather(
            w.request(
                "POST",
                f"/quiz-attempts/{attempt['id']}/submit",
                json=correct(attempt),
                headers={"If-Match": "1"},
            ),
            a.api.request(
                "PUT",
                f"/assignment-submissions/{submission['id']}/grade",
                a.campus.c_instructor,
                a.campus.c,
                json={"score": "8.00", "feedback": "Good"},
                headers={"If-Match": "1"},
            ),
        ),
        15,
    )
    assert [r.status_code for r in responses] == [200, 200]
    enrollment = ok(await w.request("GET", f"/enrollments/{w.enrollment_id}"))
    assert enrollment["enrollment"]["progress_percent"] == 100
    assert len([p for p in enrollment["progress"] if p["status"] == "completed"]) == 2
