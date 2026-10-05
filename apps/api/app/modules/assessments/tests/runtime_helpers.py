"""Real HTTP publication/enrollment setup; owner SQL only prepares timer boundaries."""

from dataclasses import dataclass
from typing import Any

import pytest
from httpx import Response
from sqlalchemy import text

from app.modules.assessments.tests.authoring_helpers import AuthoredQuiz
from tests.course_api import ok


@dataclass
class RuntimeWorld:
    authored: AuthoredQuiz
    enrollment_id: str

    @property
    def path(self) -> str:
        return f"/enrollments/{self.enrollment_id}/lessons/{self.authored.course.lesson_ids[0]}"

    async def request(self, method: str, path: str, **kwargs: Any) -> Response:
        a = self.authored
        return await a.api.request(method, path, a.campus.cse, a.campus.c, **kwargs)

    async def start(self, revision: int = 0) -> dict[str, Any]:
        return dict(
            ok(
                await self.request(
                    "POST", self.path + "/quiz-attempts", headers={"If-Match": str(revision)}
                ),
                201,
            )
        )

    async def expire(self, attempt: str) -> None:
        async with self.authored.api.factory.sessionmaker() as s, s.begin():
            await s.execute(
                text(
                    "UPDATE quiz_attempts SET started_at=clock_timestamp()-interval '120 seconds', "
                    "expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"
                ),
                {"id": attempt},
            )


def correct(
    attempt: dict[str, Any], multi: list[str] | None = None, blank: str = " PY "
) -> dict[str, Any]:
    return {
        "answers": [
            {
                "question_id": q["id"],
                "answer": {"text": blank}
                if q["question_type"] == "fill_blank"
                else {
                    "option_ids": multi
                    if q["question_type"] == "mcq_multi" and multi is not None
                    else (["a", "b"] if q["question_type"] == "mcq_multi" else ["a"])
                },
            }
            for q in attempt["questions"]
        ]
    }


async def publish_for_student(authored: AuthoredQuiz) -> RuntimeWorld:
    a = authored
    ok(await a.publish(), 201)
    ok(await a.api.assign(a.campus.author, a.campus.p, a.course.id, to=a.campus.c), 201)
    ok(
        await a.api.assign(a.campus.c_admin, a.campus.c, a.course.id, batches=[a.campus.cse_batch]),
        201,
    )
    await a.api.run_jobs()
    e = await a.api.enrollment_for(a.campus.cse, a.campus.c, a.course.id)
    assert e is not None
    return RuntimeWorld(a, e["id"])


@pytest.fixture
async def runtime(authored: AuthoredQuiz) -> RuntimeWorld:
    return await publish_for_student(authored)
