"""Legitimate HTTP setup for authoring/publication tests."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from httpx import Response

from tests.course_api import BuiltCourse, Campus, CourseApi, ok

SINGLE: dict[str, Any] = {
    "question_type": "mcq_single",
    "prompt": "Which option?",
    "options": [{"id": "a", "text": "First"}, {"id": "b", "text": "Second"}],
    "answer_key": {"correct_option_ids": ["a"]},
    "explanation": "PRIVATE explanation",
}
MULTI: dict[str, Any] = {
    "question_type": "mcq_multi",
    "prompt": "Choose two",
    "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}, {"id": "c", "text": "C"}],
    "answer_key": {"correct_option_ids": ["a", "b"]},
    "explanation": "PRIVATE multi",
}
BLANK: dict[str, Any] = {
    "question_type": "fill_blank",
    "prompt": "Language name?",
    "answer_key": {"accepted_answers": ["Python", " PY "]},
    "explanation": "PRIVATE blank",
}


@dataclass
class AuthoredQuiz:
    api: CourseApi
    campus: Campus
    course: BuiltCourse
    bank_id: str
    questions: list[dict[str, Any]]
    body: dict[str, Any]

    @property
    def quiz_path(self) -> str:
        return f"/courses/{self.course.id}/lessons/{self.course.lesson_ids[0]}/quiz"

    async def revision(self) -> int:
        return int(
            ok(
                await self.api.request(
                    "GET", f"/question-banks/{self.bank_id}", self.campus.author, self.campus.p
                )
            )["revision"]
        )

    async def request(self, method: str, path: str, **kwargs: Any) -> Response:
        if method != "GET":
            kwargs.setdefault("headers", {"If-Match": str(await self.revision())})
        return await self.api.request(method, path, self.campus.author, self.campus.p, **kwargs)

    async def put(self, **fields: Any) -> Response:
        return await self.api.request(
            "PUT", self.quiz_path, self.campus.author, self.campus.p, json={**self.body, **fields}
        )

    async def publish(self, release: str = "major") -> Response:
        return await self.api.publish(self.campus.author, self.campus.p, self.course.id, release)

    async def preview(self) -> dict[str, Any]:
        return dict(
            ok(
                await self.api.request(
                    "GET",
                    f"/courses/{self.course.id}/publish-preview",
                    self.campus.author,
                    self.campus.p,
                )
            )
        )


async def build_quiz(api: CourseApi, campus: Campus) -> AuthoredQuiz:
    bank = ok(
        await api.request(
            "POST",
            "/question-banks",
            campus.author,
            campus.p,
            json={"name": "Python questions"},
            headers={"If-Match": "0"},
        ),
        201,
    )
    questions = []
    revision = bank["revision"]
    for question_body in (SINGLE, MULTI, BLANK):
        q = ok(
            await api.request(
                "POST",
                f"/question-banks/{bank['id']}/questions",
                campus.author,
                campus.p,
                json=question_body,
                headers={"If-Match": str(revision)},
            ),
            201,
        )
        revision = q["bank_revision"]
        questions.append(q)
    course = await api.build(campus.author, campus.p, modules=(("quiz",),))
    body: dict[str, Any] = {
        "title": "Python quiz",
        "selection": {
            "mode": "manual",
            "questions": [{"question_id": q["id"], "marks": 2} for q in questions],
        },
        "pass_marks": 4,
        "time_limit_seconds": 120,
        "attempts_allowed": 2,
    }
    result = AuthoredQuiz(api, campus, course, bank["id"], questions, body)
    ok(await result.put())
    return result


def uid(value: str) -> UUID:
    return UUID(value)
