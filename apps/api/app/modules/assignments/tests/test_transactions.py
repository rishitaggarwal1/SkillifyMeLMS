"""Changed writes commit before headers or wholly roll back on failed commits."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from starlette.types import Message, Receive, Scope, Send

from app.db.session import DbSession
from app.modules.assignments.tests.history_helpers import RUBRIC, AssignmentWorld
from app.modules.identity.dependencies import CurrentPrincipal, get_tenant_session

CASES = ["definition", "submission", "upload", "grade", "publish"]


@dataclass
class Operation:
    method: str
    path: str
    body: dict[str, Any]
    revision: int
    status: int
    actor: Any
    org: Any
    visible: str


async def operation(w: AssignmentWorld, case: str) -> Operation:
    c = w.campus
    if case == "definition":
        return Operation(
            "PUT",
            f"/courses/{w.course.id}/lessons/{w.lesson_id}/assignment",
            {"title": "Durable", "max_marks": 10, "submission_kinds": ["text"], "rubric": RUBRIC},
            await w.api.factory.course_revision(w.course.id),
            200,
            c.author,
            c.p,
            "SELECT title='Durable' AND "
            "jsonb_array_length(rubric->'criteria')=2 FROM assignments WHERE "
            "lesson_id=:lid",
        )
    if case == "publish":
        return Operation(
            "POST",
            f"/courses/{w.course.id}/versions",
            {"release_type": "minor"},
            await w.api.factory.course_revision(w.course.id),
            201,
            c.author,
            c.p,
            "SELECT count(*)=2 AND max(minor)=1 FROM course_versions WHERE course_id=:cid",
        )
    if case == "upload":
        return Operation(
            "POST",
            w.path + "/submission-upload",
            {"file_name": "durable.pdf", "content_type": "application/pdf"},
            0,
            201,
            c.cse,
            c.c,
            "SELECT count(*)=1 FROM files WHERE created_by=:uid AND "
            "kind='submission' AND status='pending' AND "
            "file_name='durable.pdf'",
        )
    if case == "grade":
        sub = await w.submit()
        return Operation(
            "PUT",
            f"/assignment-submissions/{sub['id']}/grade",
            {"score": "8"},
            1,
            200,
            c.c_instructor,
            c.c,
            "SELECT s.status='graded' AND s.revision=2 AND g.score=8 AND "
            "e.progress_percent=100 AND "
            "(SELECT count(*)=1 FROM outbox_events o WHERE o.aggregate_id=e.id"
            " AND o.event_type='assignment_graded' AND "
            "o.headers->>'version'='2') "
            "FROM assignment_submissions s JOIN assignment_grades g ON "
            "g.attempt_id=s.active_attempt_id JOIN enrollments e ON "
            "e.id=s.enrollment_id WHERE e.id=:eid",
        )
    return Operation(
        "PUT",
        w.path + "/submission",
        {"submission": {"kind": "text", "text": "durable work"}},
        0,
        200,
        c.cse,
        c.c,
        "SELECT s.revision=1 AND s.text_body='durable work' AND "
        "a.text_body=s.text_body AND a.attempt_number=1 "
        "FROM assignment_submissions s JOIN submission_attempts a ON "
        "a.id=s.active_attempt_id WHERE s.enrollment_id=:eid",
    )


def params(w: AssignmentWorld) -> dict[str, UUID]:
    return {
        "cid": w.course.id,
        "lid": w.lesson_id,
        "eid": UUID(w.enrollment_id),
        "uid": w.campus.cse.id,
    }


async def snapshot(w: AssignmentWorld) -> list[Any]:
    async with w.api.factory.sessionmaker() as reader:
        rows = await reader.execute(
            text(
                "SELECT (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM "
                "assignments a WHERE a.course_id=:cid), "
                "(SELECT jsonb_agg(to_jsonb(s) ORDER BY s.id) FROM "
                "assignment_submissions s WHERE s.enrollment_id=:eid), "
                "(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM "
                "submission_attempts a JOIN assignment_submissions s ON "
                "s.id=a.submission_id WHERE s.enrollment_id=:eid), "
                "(SELECT jsonb_agg(to_jsonb(g) ORDER BY g.id) FROM "
                "assignment_grades g JOIN assignment_submissions s ON "
                "s.id=g.submission_id WHERE s.enrollment_id=:eid), "
                "(SELECT jsonb_agg(to_jsonb(f) ORDER BY f.id) FROM files f WHERE "
                "f.created_by=:uid), "
                "(SELECT revision FROM courses WHERE id=:cid), "
                "(SELECT count(*) FROM course_versions WHERE course_id=:cid), "
                "(SELECT count(*) FROM outbox_events WHERE aggregate_id IN (:eid,:cid)), "
                "(SELECT progress_percent FROM enrollments WHERE id=:eid)"
            ),
            params(w),
        )
        return list(rows.one())


@pytest.mark.parametrize("case", CASES)
async def test_write_visible_on_separate_connection_before_headers(
    assignment_world: AssignmentWorld, case: str
) -> None:
    w = assignment_world
    op = await operation(w, case)
    app = w.api.app
    writer_pid = None
    observed = False

    async def capture(principal: CurrentPrincipal, session: DbSession) -> Any:
        nonlocal writer_pid
        scoped = await get_tenant_session(principal, session)
        writer_pid = await scoped.scalar(text("SELECT pg_backend_pid()"))
        return scoped

    async def observe(scope: Scope, receive: Receive, send: Send) -> None:
        async def observe_send(message: Message) -> None:
            nonlocal observed
            if message["type"] == "http.response.start" and message["status"] == op.status:
                async with w.api.factory.sessionmaker() as reader:
                    assert writer_pid is not None
                    assert await reader.scalar(text("SELECT pg_backend_pid()")) != writer_pid
                    assert await reader.scalar(text(op.visible), params(w)) is True
                observed = True
            await send(message)

        await app(scope, receive, observe_send)

    app.dependency_overrides[get_tenant_session] = capture
    try:
        async with AsyncClient(
            transport=ASGITransport(app=observe), base_url="http://test"
        ) as client:
            response = await client.request(
                op.method,
                "/api/v1" + op.path,
                json=op.body,
                headers=w.api.h(op.actor, op.org, **{"If-Match": str(op.revision)}),
            )
        assert response.status_code == op.status, response.text
        assert observed
    finally:
        del app.dependency_overrides[get_tenant_session]


@pytest.mark.parametrize("case", CASES)
async def test_missing_stale_headers_leave_no_changes(
    assignment_world: AssignmentWorld, case: str
) -> None:
    w = assignment_world
    op = await operation(w, case)
    before = await snapshot(w)
    response = await w.api.client.request(
        op.method, "/api/v1" + op.path, json=op.body, headers=w.api.h(op.actor, op.org)
    )
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "precondition_required"
    response = await w.api.client.request(
        op.method,
        "/api/v1" + op.path,
        json=op.body,
        headers=w.api.h(op.actor, op.org, **{"If-Match": str(op.revision + 99)}),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "revision_conflict"
    assert await snapshot(w) == before


@pytest.mark.parametrize("case", CASES)
async def test_failed_commit_returns_envelope_and_rolls_back_all_changes(
    assignment_world: AssignmentWorld, case: str
) -> None:
    w = assignment_world
    op = await operation(w, case)
    before = await snapshot(w)
    app = w.api.app
    app.state.jobs.take()

    async def fail_at_commit(principal: CurrentPrincipal, session: DbSession) -> Any:
        scoped = await get_tenant_session(principal, session)
        await scoped.execute(
            text(
                "CREATE TEMP TABLE assignment_commit_probe (id integer PRIMARY KEY"
                " DEFERRABLE INITIALLY DEFERRED) ON COMMIT DROP"
            )
        )
        await scoped.execute(text("INSERT INTO assignment_commit_probe VALUES(1),(1)"))
        return scoped

    app.dependency_overrides[get_tenant_session] = fail_at_commit
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            response = await client.request(
                op.method,
                "/api/v1" + op.path,
                json=op.body,
                headers=w.api.h(op.actor, op.org, **{"If-Match": str(op.revision)}),
            )
        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "code": "internal_error",
                "message": "An unexpected error occurred.",
                "details": None,
            }
        }
        assert await snapshot(w) == before
        assert app.state.jobs.take() == []
    finally:
        del app.dependency_overrides[get_tenant_session]
