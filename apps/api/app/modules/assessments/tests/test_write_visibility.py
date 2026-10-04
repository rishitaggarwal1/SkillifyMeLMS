"""Every step 2 write is durable on another DB connection before success headers."""

from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from starlette.types import Message, Receive, Scope, Send

from app.db.session import DbSession
from app.modules.assessments.tests.authoring_helpers import SINGLE, AuthoredQuiz
from app.modules.identity.dependencies import CurrentPrincipal, get_tenant_session
from tests.course_api import ok

CASES = (
    "bank_create",
    "bank_patch",
    "bank_archive",
    "question_create",
    "question_patch",
    "question_archive",
    "skills_replace",
    "quiz_put",
    "publish",
)


@dataclass
class Mutation:
    method: str
    path: str
    body: dict[str, Any] | None
    revision: int
    status: int
    sql: str
    params: dict[str, Any]


async def mutation(a: AuthoredQuiz, case: str) -> Mutation:  # noqa: PLR0911 - enumerated endpoints
    revision = await a.revision()
    bank, qid = a.bank_id, a.questions[0]["id"]
    params: dict[str, Any] = {
        "bank": bank,
        "question": qid,
        "org": a.campus.p.id,
        "course": a.course.id,
        "lesson": a.course.lesson_ids[0],
    }
    if case == "bank_create":
        name = f"Durable {bank}"
        return Mutation(
            "POST",
            "/question-banks",
            {"name": name},
            0,
            201,
            "SELECT count(*) = 1 FROM question_banks WHERE organization_id=:org AND name=:name",
            {**params, "name": name},
        )
    if case == "bank_patch":
        return Mutation(
            "PATCH",
            f"/question-banks/{bank}",
            {"name": "Durable changed name"},
            revision,
            200,
            "SELECT name = 'Durable changed name' AND revision=:revision FROM question_banks "
            "WHERE id=:bank",
            {**params, "revision": revision + 1},
        )
    if case == "bank_archive":
        return Mutation(
            "DELETE",
            f"/question-banks/{bank}",
            None,
            revision,
            204,
            "SELECT archived_at IS NOT NULL AND revision=:revision FROM question_banks WHERE "
            "id=:bank",
            {**params, "revision": revision + 1},
        )
    if case == "question_create":
        return Mutation(
            "POST",
            f"/question-banks/{bank}/questions",
            {**SINGLE, "prompt": "Durable new question"},
            revision,
            201,
            "SELECT count(*) = 1 FROM questions q JOIN question_keys k ON k.question_id=q.id "
            "WHERE q.bank_id=:bank AND q.prompt='Durable new question' AND "
            "k.answer_key->'correct_option_ids'='[\"a\"]'::jsonb",
            params,
        )
    if case == "question_patch":
        return Mutation(
            "PATCH",
            f"/questions/{qid}",
            {"prompt": "Durable prompt", "answer_key": {"correct_option_ids": ["b"]}},
            revision,
            200,
            "SELECT q.prompt='Durable prompt' AND "
            "k.answer_key->'correct_option_ids'='[\"b\"]'::jsonb "
            "FROM questions q JOIN question_keys k ON k.question_id=q.id WHERE q.id=:question",
            params,
        )
    if case == "question_archive":
        return Mutation(
            "DELETE",
            f"/questions/{qid}",
            None,
            revision,
            204,
            "SELECT archived_at IS NOT NULL AND revision=2 FROM questions WHERE id=:question",
            params,
        )
    if case == "skills_replace":
        previous, skill = await a.api.factory.skill(), await a.api.factory.skill()
        ok(
            await a.request(
                "PUT", f"/questions/{qid}/skills", json={"skill_ids": [str(previous.id)]}
            )
        )
        return Mutation(
            "PUT",
            f"/questions/{qid}/skills",
            {"skill_ids": [str(skill.id)]},
            await a.revision(),
            200,
            "SELECT count(*) = 1 AND bool_and(skill_id=:skill) FROM question_skills WHERE "
            "question_id=:question",
            {**params, "skill": skill.id},
        )
    revision = await a.api.factory.course_revision(a.course.id)
    if case == "quiz_put":
        return Mutation(
            "PUT",
            a.quiz_path,
            {**a.body, "title": "Durable quiz"},
            revision,
            200,
            "SELECT q.title='Durable quiz' AND c.revision=:revision AND "
            "l.content->>'quiz_id'=q.id::text "
            "FROM quizzes q JOIN courses c ON c.id=q.course_id JOIN lessons l ON "
            "l.id=q.lesson_id WHERE q.lesson_id=:lesson",
            {**params, "revision": revision + 1},
        )
    return Mutation(
        "POST",
        f"/courses/{a.course.id}/versions",
        {"release_type": "major"},
        revision,
        201,
        "SELECT count(*) = 3 FROM quiz_version_questions q JOIN quiz_version_keys k ON "
        "k.question_id=q.id "
        "JOIN quiz_versions v ON v.id=q.quiz_version_id JOIN courses c ON "
        "c.current_version_id=v.course_version_id "
        "JOIN course_versions cv ON cv.id=v.course_version_id "
        "WHERE c.id=:course AND "
        "cv.snapshot->'modules'->0->'lessons'->0->'content'->>'quiz_id'=v.quiz_id::text",
        params,
    )


@pytest.mark.parametrize("case", CASES)
async def test_each_write_visible_at_success_headers(authored: AuthoredQuiz, case: str) -> None:
    a = authored
    operation = await mutation(a, case)
    app = a.api.app
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
            if message["type"] == "http.response.start" and message["status"] == operation.status:
                async with a.api.factory.sessionmaker() as reader:
                    assert writer_pid is not None
                    assert await reader.scalar(text("SELECT pg_backend_pid()")) != writer_pid
                    assert await reader.scalar(text(operation.sql), operation.params) is True
                observed = True
            await send(message)

        await app(scope, receive, observe_send)

    app.dependency_overrides[get_tenant_session] = capture
    try:
        async with AsyncClient(
            transport=ASGITransport(app=observe), base_url="http://test"
        ) as client:
            response = await client.request(
                operation.method,
                f"/api/v1{operation.path}",
                headers=a.api.h(
                    a.campus.author, a.campus.p, **{"If-Match": str(operation.revision)}
                ),
                json=operation.body,
            )
        assert response.status_code == operation.status, response.text
        assert observed
    finally:
        del app.dependency_overrides[get_tenant_session]


@pytest.mark.parametrize("case", CASES)
async def test_each_write_requires_current_revision(authored: AuthoredQuiz, case: str) -> None:
    a = authored
    op = await mutation(a, case)
    bank_revision = await a.revision()
    course_revision = await a.api.factory.course_revision(a.course.id)
    missing = await a.api.client.request(
        op.method, f"/api/v1{op.path}", headers=a.api.h(a.campus.author, a.campus.p), json=op.body
    )
    assert missing.status_code == 428, missing.text
    assert missing.json()["error"]["code"] == "precondition_required"
    stale = await a.api.client.request(
        op.method,
        f"/api/v1{op.path}",
        headers=a.api.h(a.campus.author, a.campus.p, **{"If-Match": str(op.revision + 1000)}),
        json=op.body,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "revision_conflict"
    assert await a.revision() == bank_revision
    assert await a.api.factory.course_revision(a.course.id) == course_revision
