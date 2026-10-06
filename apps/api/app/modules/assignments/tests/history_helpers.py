"""Full assignment setup through existing authoring/publication/student APIs."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from httpx import Response

from tests.course_api import BuiltCourse, Campus, CourseApi, ok

RUBRIC = {
    "criteria": [
        {"id": "correct", "label": "Correctness", "max_marks": "6"},
        {"id": "clear", "label": "Clarity", "max_marks": "4"},
    ]
}


@dataclass
class AssignmentWorld:
    api: CourseApi
    campus: Campus
    course: BuiltCourse
    enrollment_id: str

    @property
    def lesson_id(self) -> UUID:
        return self.course.lesson_ids[0]

    @property
    def path(self) -> str:
        return f"/enrollments/{self.enrollment_id}/lessons/{self.lesson_id}"

    async def mine(self, method: str, suffix: str, **kwargs: Any) -> Response:
        return await self.api.request(
            method, self.path + "/" + suffix, self.campus.cse, self.campus.c, **kwargs
        )

    async def submit(self, revision: int = 0, text: str = "work") -> dict[str, Any]:
        return dict(
            ok(
                await self.mine(
                    "PUT",
                    "submission",
                    headers={"If-Match": str(revision)},
                    json={"submission": {"kind": "text", "text": text}},
                )
            )
        )

    async def grade(self, sid: str, revision: int, **fields: Any) -> Response:
        return await self.api.request(
            "PUT",
            f"/assignment-submissions/{sid}/grade",
            self.campus.c_instructor,
            self.campus.c,
            headers={"If-Match": str(revision)},
            json=fields,
        )


async def create_world(api: CourseApi, campus: Campus, **definition: Any) -> AssignmentWorld:
    course = await api.build(campus.author, campus.p, modules=(("assignment",),))
    if definition:
        ok(
            await api.define_assignment(
                campus.author, campus.p, course.id, course.lesson_ids[0], **definition
            )
        )
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    ok(await api.assign(campus.author, campus.p, course.id, to=campus.c), 201)
    ok(await api.assign(campus.c_admin, campus.c, course.id, batches=[campus.cse_batch]), 201)
    await api.run_jobs()
    enrollment = await api.enrollment_for(campus.cse, campus.c, course.id)
    assert enrollment is not None
    return AssignmentWorld(api, campus, course, enrollment["id"])


@pytest.fixture
async def assignment_world(api: CourseApi, campus: Campus) -> AssignmentWorld:
    return await create_world(api, campus)
