"""Raw app-role protections, DB clock after locks, frozen draws and exact grading."""

import asyncio
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import DbSession
from app.modules.assessments.tests.authoring_helpers import AuthoredQuiz
from app.modules.assessments.tests.runtime_helpers import RuntimeWorld, correct, publish_for_student
from app.modules.enrollments.tasks import run_reconcile_course_org
from app.modules.identity.dependencies import CurrentPrincipal, get_tenant_session
from tests.course_api import ok
from tests.fixtures import TenantSessionFactory


async def test_autosave_query_count_does_not_grow_with_answer_batch(runtime: RuntimeWorld) -> None:
    w = runtime
    attempt = await w.start()
    engine: AsyncEngine = w.authored.api.app.state.engine
    statements: list[str] = []

    def capture(
        _conn: Any, _cursor: Any, statement: str, _params: Any, _context: Any, _many: bool
    ) -> None:
        statements.append(statement)

    batches = [correct(attempt)["answers"][:1], correct(attempt)["answers"]]
    counts: list[int] = []
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        for revision, answers in enumerate(batches, start=1):
            statements.clear()
            saved = ok(
                await w.request(
                    "PUT",
                    f"/quiz-attempts/{attempt['id']}/answers",
                    json={"answers": answers},
                    headers={"If-Match": str(revision)},
                )
            )
            assert saved["revision"] == revision + 1
            assert sum("app.quiz_save_answers(" in s for s in statements) == 1
            counts.append(len(statements))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert counts[0] > 0
    assert counts[1] == counts[0], counts


async def test_raw_functions_do_not_bypass_owner_deadline_or_private_execute(
    runtime: RuntimeWorld, tenant_session: TenantSessionFactory
) -> None:
    w, a = runtime, runtime.authored
    attempt = await w.start()
    aid = UUID(attempt["id"])
    async with tenant_session(org=a.campus.c.id, user=a.campus.ece.id) as s:
        assert await s.scalar(text("SELECT app.quiz_submit(:id,1,'[]'::jsonb)"), {"id": aid}) == {
            "error": "not_found"
        }
        assert (
            await s.execute(text("SELECT * FROM quiz_attempts WHERE id=:id"), {"id": aid})
        ).all() == []
        assert (
            await s.execute(text("SELECT * FROM quiz_answers WHERE attempt_id=:id"), {"id": aid})
        ).all() == []
    async with tenant_session(org=a.campus.c.id, user=a.campus.cse.id) as s:
        assert (
            await s.scalar(
                text(
                    "SELECT has_function_privilege(current_user,"
                    "'app.quiz_mutate(uuid,integer,jsonb,boolean,boolean)','EXECUTE')"
                )
            )
            is False
        )
        assert await s.scalar(text("SELECT app.quiz_finalize_due(:id)"), {"id": aid}) == {
            "error": "not_found"
        }
        assert (
            await s.scalar(
                text("SELECT has_table_privilege(current_user,'quiz_attempts','UPDATE')")
            )
            is False
        )
        assert (
            await s.scalar(text("SELECT has_table_privilege(current_user,'quiz_answers','INSERT')"))
            is False
        )
        assert (
            await s.execute(
                text(
                    "SELECT k.* FROM quiz_version_keys k "
                    "JOIN quiz_version_questions p ON p.id=k.question_id"
                )
            )
        ).all() == []
    await w.expire(attempt["id"])
    async with tenant_session(org=a.campus.c.id, user=a.campus.cse.id) as s:
        assert await s.scalar(
            text("SELECT app.quiz_save_answers(:id,1,'[]'::jsonb)"), {"id": aid}
        ) == {"error": "attempt_expired"}


async def test_authorization_hides_same_batch_students_attempts(runtime: RuntimeWorld) -> None:
    w, a = runtime, runtime.authored
    other = await a.api.factory.member(a.campus.c, "student")
    await a.api.factory.add_to_batch(a.campus.cse_batch, other)
    await run_reconcile_course_org(a.api.app.state.sessionmaker, a.course.id, a.campus.c.id)
    attempt = await w.start()
    for method, suffix in [
        ("GET", ""),
        ("GET", "/results"),
        ("PUT", "/answers"),
        ("POST", "/submit"),
    ]:
        response = await a.api.request(
            method,
            f"/quiz-attempts/{attempt['id']}{suffix}",
            other,
            a.campus.c,
            json={"answers": []} if method != "GET" else None,
            headers={"If-Match": "1"},
        )
        assert response.status_code == 404


@pytest.mark.parametrize("sensitive", [False, True])
async def test_blank_unicode_casefold_and_nfc(authored: AuthoredQuiz, sensitive: bool) -> None:
    a = authored
    ok(
        await a.request(
            "PATCH",
            f"/questions/{a.questions[2]['id']}",
            json={
                "answer_key": {
                    "accepted_answers": ["Stra\u00dfe", "\u00e9"],
                    "case_sensitive": sensitive,
                }
            },
        )
    )
    w = await publish_for_student(a)
    attempt = await w.start()
    result = ok(
        await w.request(
            "POST",
            f"/quiz-attempts/{attempt['id']}/submit",
            json=correct(attempt, blank=" STRASSE "),
            headers={"If-Match": "1"},
        )
    )
    assert result["score"] == ("4.00" if sensitive else "6.00")
    attempt = await w.start(1)
    result = ok(
        await w.request(
            "POST",
            f"/quiz-attempts/{attempt['id']}/submit",
            json=correct(attempt, blank="e\u0301"),
            headers={"If-Match": "1"},
        )
    )
    assert result["score"] == "6.00"


@pytest.mark.parametrize(("marks", "score"), [("1.00", "0.50"), ("0.01", "0.01")])
async def test_decimal_half_up(authored: AuthoredQuiz, marks: str, score: str) -> None:
    a = authored
    ok(
        await a.put(
            selection={
                "mode": "manual",
                "questions": [{"question_id": a.questions[1]["id"], "marks": marks}],
            },
            pass_marks=marks,
        )
    )
    w = await publish_for_student(a)
    attempt = await w.start()
    result = ok(
        await w.request(
            "POST",
            f"/quiz-attempts/{attempt['id']}/submit",
            json=correct(attempt, multi=["a"]),
            headers={"If-Match": "1"},
        )
    )
    assert result["score"] == score


async def test_bank_draw_and_order_are_saved_once(authored: AuthoredQuiz) -> None:
    a = authored
    ok(
        await a.put(
            selection={
                "mode": "bank",
                "bank_id": a.bank_id,
                "draw_count": 2,
                "marks_per_question": 2,
            },
            randomize_order=True,
        )
    )
    w = await publish_for_student(a)
    attempt = await w.start()
    assert len(attempt["questions"]) == 2
    resumed = ok(await w.request("GET", f"/quiz-attempts/{attempt['id']}"))
    assert resumed["questions"] == attempt["questions"]
    ok(await a.request("DELETE", f"/questions/{a.questions[0]['id']}"), 204)
    again = ok(await w.request("GET", f"/quiz-attempts/{attempt['id']}"))
    assert again["questions"] == attempt["questions"]


async def test_clock_checked_after_waiting_for_enrollment_lock(runtime: RuntimeWorld) -> None:
    w = runtime
    attempt = await w.start()
    app = w.authored.api.app
    ready = asyncio.Event()
    pid: int | None = None

    async def capture(principal: CurrentPrincipal, session: DbSession) -> Any:
        nonlocal pid
        scoped = await get_tenant_session(principal, session)
        pid = await scoped.scalar(text("SELECT pg_backend_pid()"))
        ready.set()
        return scoped

    app.dependency_overrides[get_tenant_session] = capture
    task: asyncio.Task[Any] | None = None
    try:
        async with w.authored.api.factory.sessionmaker() as blocker, blocker.begin():
            await blocker.execute(
                text("SELECT id FROM enrollments WHERE id=:id FOR UPDATE"),
                {"id": UUID(w.enrollment_id)},
            )
            task = asyncio.create_task(
                w.request(
                    "PUT",
                    f"/quiz-attempts/{attempt['id']}/answers",
                    json=correct(attempt),
                    headers={"If-Match": "1"},
                )
            )
            await asyncio.wait_for(ready.wait(), 5)
            for _ in range(200):
                waiting = await blocker.scalar(
                    text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"),
                    {"pid": pid},
                )
                if waiting:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Answer write never reached the enrollment lock")
            await blocker.execute(
                text(
                    "UPDATE quiz_attempts SET started_at=clock_timestamp()-interval '120 seconds', "
                    "expires_at=clock_timestamp() WHERE id=:id"
                ),
                {"id": UUID(attempt["id"])},
            )
        response = await asyncio.wait_for(task, 5)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "attempt_expired"
        resumed = ok(await w.request("GET", f"/quiz-attempts/{attempt['id']}"))
        assert resumed["revision"] == 1
        assert all(q["saved_answer"] is None for q in resumed["questions"])
    finally:
        del app.dependency_overrides[get_tenant_session]
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
