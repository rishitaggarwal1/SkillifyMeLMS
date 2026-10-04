"""Real bank locks serialize revisions and freeze coherent prompt/key pairs."""

import asyncio
from collections.abc import Sequence
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assessments.models import QuestionBank, QuizVersionKey, QuizVersionQuestion
from app.modules.assessments.repository import AuthorRepository
from app.modules.assessments.service import QuizContentSource
from app.modules.assessments.tests.authoring_helpers import AuthoredQuiz
from tests.course_api import ok


async def test_concurrent_question_edits_have_one_winner(authored: AuthoredQuiz) -> None:
    a = authored
    revision = await a.revision()
    path = f"/questions/{a.questions[0]['id']}"
    responses = await asyncio.gather(
        *[
            a.request("PATCH", path, json={"prompt": prompt}, headers={"If-Match": str(revision)})
            for prompt in ("First concurrent edit", "Second concurrent edit")
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(r.json() for r in responses if r.status_code == 200)
    assert ok(await a.request("GET", path))["prompt"] == winner["prompt"]
    assert await a.revision() == revision + 1


async def test_publish_bank_lock_keeps_prompt_and_key_coherent(
    authored: AuthoredQuiz, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = authored
    locked, release, edit_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = QuizContentSource.lock

    async def gated_lock(
        self: QuizContentSource, session: AsyncSession, ids: Sequence[UUID]
    ) -> None:
        await original(self, session, ids)
        locked.set()
        await asyncio.wait_for(release.wait(), timeout=10)

    # Signal that the real competing request has reached its bank mutation.

    original_bump = AuthorRepository.bump_bank

    async def track_bump(
        self: AuthorRepository, bank_id: UUID, expected: int
    ) -> QuestionBank | None:
        edit_entered.set()
        return await original_bump(self, bank_id, expected)

    monkeypatch.setattr(QuizContentSource, "lock", gated_lock)
    monkeypatch.setattr(AuthorRepository, "bump_bank", track_bump)
    publish = asyncio.create_task(a.publish())
    edit = None
    try:
        await asyncio.wait_for(locked.wait(), timeout=10)
        edit = asyncio.create_task(
            a.request(
                "PATCH",
                f"/questions/{a.questions[0]['id']}",
                json={
                    "prompt": "Concurrent new prompt",
                    "answer_key": {"correct_option_ids": ["b"]},
                },
                headers={"If-Match": str(await a.revision())},
            )
        )
        await asyncio.wait_for(edit_entered.wait(), timeout=10)
        assert not edit.done()
        release.set()
        version = ok(await asyncio.wait_for(publish, timeout=10), 201)
        ok(await asyncio.wait_for(edit, timeout=10))
    finally:
        release.set()
        await asyncio.gather(publish, *([edit] if edit else []), return_exceptions=True)
    async with a.api.factory.sessionmaker() as reader:
        rows = await reader.execute(
            select(QuizVersionQuestion, QuizVersionKey)
            .join(QuizVersionKey, QuizVersionKey.question_id == QuizVersionQuestion.id)
            .where(QuizVersionQuestion.question_id == UUID(a.questions[0]["id"]))
        )
        question, key = rows.one()
        assert question.prompt == a.questions[0]["prompt"]
        assert key.answer_key["correct_option_ids"] == ["a"]
    path = f"/courses/{a.course.id}/versions/{version['id']}/lessons/{a.course.lesson_ids[0]}/quiz"
    assert ok(await a.request("GET", path))["questions"][0]["prompt"] == a.questions[0]["prompt"]
