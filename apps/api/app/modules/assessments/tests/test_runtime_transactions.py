"""All three runtime writes commit before success headers; failed commits leave no work."""

from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from starlette.types import Message, Receive, Scope, Send

from app.db.session import DbSession
from app.modules.assessments.tests.runtime_helpers import RuntimeWorld, correct
from app.modules.identity.dependencies import CurrentPrincipal, get_tenant_session


async def operation(w: RuntimeWorld, case: str) -> tuple[str, str, dict[str, Any], int, int, str]:
    if case == "start":
        return (
            "POST",
            w.path + "/quiz-attempts",
            {},
            0,
            201,
            "SELECT count(*)=1 FROM quiz_attempts WHERE enrollment_id=:eid",
        )
    a = await w.start()
    if case == "save":
        return (
            "PUT",
            f"/quiz-attempts/{a['id']}/answers",
            correct(a),
            1,
            200,
            (
                "SELECT a.revision=2 AND count(b.id)=3 FROM quiz_attempts a "
                "JOIN quiz_answers b ON b.attempt_id=a.id WHERE a.enrollment_id=:eid "
                "GROUP BY a.revision"
            ),
        )
    return (
        "POST",
        f"/quiz-attempts/{a['id']}/submit",
        correct(a),
        1,
        200,
        (
            "SELECT a.state='submitted' AND a.score=6 AND e.progress_percent=100 AND "
            "(SELECT count(*)=1 FROM outbox_events WHERE aggregate_id=e.id "
            "AND event_type='quiz_attempt_submitted') FROM quiz_attempts a "
            "JOIN enrollments e ON e.id=a.enrollment_id WHERE e.id=:eid"
        ),
    )


@pytest.mark.parametrize("case", ["start", "save", "submit"])
async def test_runtime_write_visible_before_success_headers(
    runtime: RuntimeWorld, case: str
) -> None:
    w = runtime
    method, path, body, revision, status, sql = await operation(w, case)
    app = w.authored.api.app
    writer_pid: int | None = None
    observed = False

    async def capture(principal: CurrentPrincipal, session: DbSession) -> Any:
        nonlocal writer_pid
        scoped = await get_tenant_session(principal, session)
        writer_pid = await scoped.scalar(text("SELECT pg_backend_pid()"))
        return scoped

    async def observe(scope: Scope, receive: Receive, send: Send) -> None:
        async def observe_send(message: Message) -> None:
            nonlocal observed
            if message["type"] == "http.response.start" and message["status"] == status:
                async with w.authored.api.factory.sessionmaker() as reader:
                    assert writer_pid is not None
                    assert await reader.scalar(text("SELECT pg_backend_pid()")) != writer_pid
                    assert await reader.scalar(text(sql), {"eid": UUID(w.enrollment_id)}) is True
                observed = True
            await send(message)

        await app(scope, receive, observe_send)

    app.dependency_overrides[get_tenant_session] = capture
    try:
        async with AsyncClient(
            transport=ASGITransport(app=observe), base_url="http://test"
        ) as client:
            response = await client.request(
                method,
                "/api/v1" + path,
                json=body,
                headers=w.authored.api.h(
                    w.authored.campus.cse, w.authored.campus.c, **{"If-Match": str(revision)}
                ),
            )
        assert response.status_code == status, response.text
        assert observed
    finally:
        del app.dependency_overrides[get_tenant_session]


@pytest.mark.parametrize("case", ["start", "save", "submit"])
async def test_runtime_missing_and_stale_revision(runtime: RuntimeWorld, case: str) -> None:
    method, path, body, revision, _status, _sql = await operation(runtime, case)
    api, a = runtime.authored.api, runtime.authored
    response = await api.client.request(
        method, "/api/v1" + path, json=body, headers=api.h(a.campus.cse, a.campus.c)
    )
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "precondition_required"
    response = await runtime.request(
        method, path, json=body, headers={"If-Match": str(revision + 99)}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_revision"


@pytest.mark.parametrize("case", ["start", "save", "submit"])
async def test_failed_commit_rolls_back_runtime_and_drops_expiry_hooks(
    runtime: RuntimeWorld, case: str
) -> None:
    w = runtime
    method, path, body, revision, _status, _sql = await operation(w, case)
    app = w.authored.api.app
    app.state.jobs.take()

    async def fail_at_commit(principal: CurrentPrincipal, session: DbSession) -> Any:
        scoped = await get_tenant_session(principal, session)
        await scoped.execute(
            text(
                "CREATE TEMP TABLE quiz_commit_probe "
                "(id integer PRIMARY KEY DEFERRABLE INITIALLY DEFERRED) ON COMMIT DROP"
            )
        )
        await scoped.execute(text("INSERT INTO quiz_commit_probe(id) VALUES(1),(1)"))
        return scoped

    app.dependency_overrides[get_tenant_session] = fail_at_commit
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            response = await client.request(
                method,
                "/api/v1" + path,
                json=body,
                headers=w.authored.api.h(
                    w.authored.campus.cse, w.authored.campus.c, **{"If-Match": str(revision)}
                ),
            )
        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "code": "internal_error",
                "message": "An unexpected error occurred.",
                "details": None,
            }
        }
        assert app.state.jobs.take() == []
        async with w.authored.api.factory.sessionmaker() as reader:
            params = {"eid": UUID(w.enrollment_id)}
            if case == "start":
                assert (
                    await reader.scalar(
                        text("SELECT count(*) FROM quiz_attempts WHERE enrollment_id=:eid"), params
                    )
                    == 0
                )
            else:
                row = (
                    await reader.execute(
                        text("SELECT revision,state FROM quiz_attempts WHERE enrollment_id=:eid"),
                        params,
                    )
                ).one()
                assert row.revision == 1
                assert row.state == "in_progress"
            assert (
                await reader.scalar(
                    text(
                        "SELECT count(*) FROM quiz_answers b "
                        "JOIN quiz_attempts a ON a.id=b.attempt_id "
                        "WHERE a.enrollment_id=:eid"
                    ),
                    params,
                )
                == 0
            )
            assert (
                await reader.scalar(
                    text(
                        "SELECT count(*) FROM outbox_events WHERE aggregate_id=:eid "
                        "AND event_type='quiz_attempt_submitted'"
                    ),
                    params,
                )
                == 0
            )
    finally:
        del app.dependency_overrides[get_tenant_session]
