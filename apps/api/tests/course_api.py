"""Helpers for course / enrollment API tests (loaded as a pytest plugin).

`campus` is a fresh world per test, so tests can publish, assign and enroll freely:

    P  publisher: author (instructor), admin (org_admin), student in p_batch
    C  college: admin, instructor, students cse (CSE batch) and ece (ECE batch)
    O  unrelated college: admin

`api` wraps the HTTP calls as a given user in a given org; `api.run_jobs()` runs the background jobs
the requests enqueued (enrollment fan-out, upgrades) the way the worker would.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from uuid_utils.compat import uuid7

from app.modules.identity.models import Batch, Organization, User
from tests.factories import Factory
from tests.fakes import RecordingJobQueue
from tests.fixtures import AuthHeaders
from tests.jobs import run_jobs


@dataclass
class Campus:
    p: Organization
    c: Organization
    o: Organization
    author: User
    p_admin: User
    p_student: User
    c_admin: User
    c_instructor: User
    cse: User
    ece: User
    o_admin: User
    platform_admin: User
    p_batch: Batch
    cse_batch: Batch
    ece_batch: Batch
    o_batch: Batch


@pytest.fixture
async def campus(factory: Factory) -> Campus:
    p = await factory.org(name="Publisher", publisher=True)
    c, o = await factory.org(name="College"), await factory.org(name="Other")
    p_batch = await factory.batch(p)
    cse_batch, ece_batch = await factory.batch(c, name="CSE"), await factory.batch(c, name="ECE")
    o_batch = await factory.batch(o)
    p_student = await factory.member(p, "student")
    cse, ece = await factory.member(c, "student"), await factory.member(c, "student")
    await factory.add_to_batch(p_batch, p_student)
    await factory.add_to_batch(cse_batch, cse)
    await factory.add_to_batch(ece_batch, ece)
    return Campus(
        p=p, c=c, o=o,
        author=await factory.member(p, "instructor"),
        p_admin=await factory.member(p, "org_admin"),
        p_student=p_student,
        c_admin=await factory.member(c, "org_admin"),
        c_instructor=await factory.member(c, "instructor"),
        cse=cse, ece=ece,
        o_admin=await factory.member(o, "org_admin"),
        platform_admin=await factory.user(),
        p_batch=p_batch, cse_batch=cse_batch, ece_batch=ece_batch, o_batch=o_batch,
    )  # fmt: skip


@dataclass
class BuiltCourse:
    id: UUID
    module_ids: list[UUID]
    lesson_ids: list[UUID]  # in outline order
    lessons_by_module: dict[UUID, list[UUID]] = field(default_factory=dict)


_OUTLINE_EDIT = re.compile(r"^/courses/(?P<course>[0-9a-f-]{36})/(modules|lessons)(/|$)")


def ok(response: Response, status: int = 200) -> Any:
    assert response.status_code == status, response.text[:500]
    return response.json() if response.content else None


@dataclass
class CourseApi:
    client: AsyncClient
    auth_headers: AuthHeaders
    factory: Factory
    app: FastAPI

    def h(self, user: User, org: Organization, **headers: str) -> dict[str, str]:
        return {**self.auth_headers(user, org=org.id), **headers}

    async def request(
        self, method: str, path: str, user: User, org: Organization, **kwargs: Any
    ) -> Response:
        extra: dict[str, str] = kwargs.pop("headers", {})
        # Outline and lesson edits require If-Match. Send the current revision unless the test
        # sets the header itself (tests of the 428/409 behaviour call the client directly).
        outline = _OUTLINE_EDIT.match(path)
        if outline and method != "GET" and "If-Match" not in extra:
            extra["If-Match"] = str(await self.factory.course_revision(UUID(outline["course"])))
        headers = self.h(user, org, **extra)
        return await self.client.request(method, f"/api/v1{path}", headers=headers, **kwargs)

    async def lesson_content(self, org: Organization, lesson_type: str) -> dict[str, Any]:
        if lesson_type == "video":
            video = await self.factory.video(org, status="ready", duration=120)
            return {"video_asset_id": str(video.id)}
        if lesson_type == "pdf":
            return {"file_id": str((await self.factory.pdf(org)).id)}
        if lesson_type == "notes":
            return {"doc": {"type": "doc", "content": []}}
        return {}

    async def build(
        self,
        user: User,
        org: Organization,
        modules: Sequence[Sequence[str]] = (("video", "notes", "pdf"),),
        *,
        title: str | None = None,
        public: bool = False,
    ) -> BuiltCourse:
        """Create a course with one module per entry, each with lessons of the given types."""
        course = ok(
            await self.request(
                "POST",
                "/courses",
                user,
                org,
                json={"title": title or f"Course {uuid7().hex[-8:]}", "is_public_catalog": public},
            ),
            201,
        )
        built = BuiltCourse(id=UUID(course["id"]), module_ids=[], lesson_ids=[])
        for index, lesson_types in enumerate(modules, start=1):
            module = ok(
                await self.request(
                    "POST", f"/courses/{built.id}/modules", user, org,
                    json={"title": f"Module {index}"},
                ),
                201,
            )  # fmt: skip
            module_id = UUID(module["id"])
            built.module_ids.append(module_id)
            built.lessons_by_module[module_id] = []
            for lesson_type in lesson_types:
                lesson = ok(
                    await self.request(
                        "POST", f"/courses/{built.id}/modules/{module_id}/lessons", user, org,
                        json={
                            "title": f"{lesson_type} lesson",
                            "lesson_type": lesson_type,
                            "content": await self.lesson_content(org, lesson_type),
                        },
                    ),
                    201,
                )  # fmt: skip
                built.lesson_ids.append(UUID(lesson["id"]))
                built.lessons_by_module[module_id].append(UUID(lesson["id"]))
        return built

    async def publish(
        self, user: User, org: Organization, course_id: UUID, release_type: str = "major"
    ) -> Response:
        return await self.request(
            "POST", f"/courses/{course_id}/versions", user, org,
            json={"release_type": release_type},
        )  # fmt: skip

    async def assign(
        self,
        user: User,
        org: Organization,
        course_id: UUID,
        *,
        to: Organization | None = None,
        batches: Sequence[Batch] = (),
    ) -> Response:
        body: dict[str, Any] = {"batch_ids": [str(b.id) for b in batches]}
        if to is not None:
            body["organization_id"] = str(to.id)
        return await self.request("POST", f"/courses/{course_id}/assignments", user, org, json=body)

    async def run_jobs(self) -> int:
        queue: RecordingJobQueue = self.app.state.jobs
        return await run_jobs(queue, self.app.state.sessionmaker)

    async def enrollments(self, student: User, org: Organization) -> list[dict[str, Any]]:
        page = ok(await self.request("GET", "/enrollments", student, org))
        items: list[dict[str, Any]] = page["items"]
        return items

    async def enrollment_for(
        self, student: User, org: Organization, course_id: UUID
    ) -> dict[str, Any] | None:
        return next(
            (e for e in await self.enrollments(student, org) if e["course_id"] == str(course_id)),
            None,
        )


@pytest.fixture
def api(
    client: AsyncClient,
    auth_headers: AuthHeaders,
    factory: Factory,
    app: FastAPI,
    jobs: RecordingJobQueue,
) -> CourseApi:
    del jobs  # cleared for this test
    return CourseApi(client=client, auth_headers=auth_headers, factory=factory, app=app)


async def published_for_cse(api: CourseApi, campus: Campus, **build: Any) -> BuiltCourse:
    """A published course by the publisher, granted to C and distributed to CSE, with enrollments
    fanned out."""
    course = await api.build(campus.author, campus.p, **build)
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    ok(await api.assign(campus.author, campus.p, course.id, to=campus.c), 201)
    ok(await api.assign(campus.c_admin, campus.c, course.id, batches=[campus.cse_batch]), 201)
    await api.run_jobs()
    return course
