"""Role checks for every /api/v1 endpoint.

Each route is called as seven callers: anonymous, student, lab_author, instructor and org_admin of
org A, the org_admin of an unrelated org B (acting in B), and a platform admin (acting in A).

Expected: allowed & same org -> 2xx; insufficient permission -> 403; anonymous -> 401. Org B's
admin gets 2xx on collection routes (their own org's data), 404 on routes naming org A's resources,
and 403 where org admins aren't allowed at all.

`test_every_route_is_in_the_matrix` fails when a new endpoint is added without a row here.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from uuid_utils.compat import uuid7

from app.db.base import new_id
from app.modules.assessments.models import (
    Question,
    QuestionBank,
    QuestionKey,
    Quiz,
    QuizVersion,
    QuizVersionQuestion,
)
from app.modules.identity.models import Organization, User
from tests.factories import Factory
from tests.fakes import EnqueueRecorder, FakeKeycloakAdmin
from tests.fixtures import AuthHeaders

ROLES = (
    "anonymous",
    "student",
    "lab_author",
    "instructor",
    "org_admin",
    "other_admin",
    "platform_admin",
)
EVERYONE = {"student", "lab_author", "instructor", "org_admin", "other_admin", "platform_admin"}
ADMINS = {"org_admin", "other_admin", "platform_admin"}
STAFF_READ = {"instructor", "org_admin", "other_admin", "platform_admin"}
PLATFORM = {"platform_admin"}


@dataclass
class World:
    factory: Factory
    org: Organization
    users: dict[str, User]
    other_org: Organization


Request = tuple[str, dict[str, Any]]  # (path, httpx request kwargs)
Builder = Callable[[World], Awaitable[Request]]


async def _static(path: str, **kwargs: Any) -> Request:
    return path, kwargs


def at(path: str, **kwargs: Any) -> Builder:
    async def build(_: World) -> Request:
        return path, dict(kwargs)

    return build


async def _new_org(w: World) -> Request:
    return f"/api/v1/organizations/{(await w.factory.org()).id}", {}


async def _batch(w: World) -> Request:
    return f"/api/v1/batches/{(await w.factory.batch(w.org)).id}", {}


async def _batch_members(w: World) -> Request:
    batch = await w.factory.batch(w.org)
    return f"/api/v1/batches/{batch.id}/members", {}


async def _add_batch_member(w: World) -> Request:
    batch = await w.factory.batch(w.org)
    student = await w.factory.member(w.org, "student")
    return f"/api/v1/batches/{batch.id}/members", {"json": {"user_ids": [str(student.id)]}}


async def _remove_batch_member(w: World) -> Request:
    batch = await w.factory.batch(w.org)
    student = await w.factory.member(w.org, "student")
    await w.factory.add_to_batch(batch, student)
    return f"/api/v1/batches/{batch.id}/members/{student.id}", {}


async def _member(w: World) -> Request:
    return f"/api/v1/members/{(await w.factory.member(w.org, 'student')).id}", {}


async def _patch_member(w: World) -> Request:
    path, _ = await _member(w)
    return path, {"json": {"roles": ["student", "instructor"]}}


async def _invitation(w: World) -> Request:
    user = await w.factory.user(status="invited")
    await w.factory.member(w.org, "student", user=user)
    inv = await w.factory.invitation(w.org, user=user)
    return f"/api/v1/invitations/{inv.id}", {}


async def _resend(w: World) -> Request:
    path, _ = await _invitation(w)
    return f"{path}/resend", {}


async def _import(w: World) -> Request:
    return f"/api/v1/imports/{(await w.factory.import_job(w.org)).id}", {}


async def _import_errors(w: World) -> Request:
    path, _ = await _import(w)
    return f"{path}/errors.csv", {}


def _invite_body(_: World) -> Awaitable[Request]:
    return _static(
        "/api/v1/invitations",
        json={"email": f"m-{uuid7().hex[-8:]}@college.test", "roles": ["student"]},
    )


def _org_body(_: World) -> Awaitable[Request]:
    return _static("/api/v1/organizations", json={"name": "M", "slug": f"m-{uuid7().hex[-10:]}"})


def _batch_body(_: World) -> Awaitable[Request]:
    return _static("/api/v1/batches", json={"name": f"B {uuid7().hex[-8:]}"})


def _import_body(_: World) -> Awaitable[Request]:
    return _static(
        "/api/v1/imports",
        files={"file": ("s.csv", b"email,full_name\na@college.test,A\n", "text/csv")},
    )


# ---------------------------------------------------------------------------- Phase 2 builders


async def _course(w: World, *, published: bool = False) -> tuple[Any, Any, Any]:
    """A course owned by org A with one module and one notes lesson (optionally published)."""
    course = await w.factory.course(w.org)
    module = await w.factory.module(course)
    lesson = await w.factory.lesson(module, lesson_type="notes")
    if published:
        await w.factory.version(course, lesson)
    return course, module, lesson


async def _course_path(w: World) -> Request:
    course, _, _ = await _course(w)
    return f"/api/v1/courses/{course.id}", {}


def _course_sub(suffix: str, *, published: bool = False, **kwargs: Any) -> Builder:
    async def build(w: World) -> Request:
        course, module, lesson = await _course(w, published=published)
        path = suffix.format(m=module.id, lesson=lesson.id)
        body = kwargs.get("json")
        if callable(body):
            body = body(course, module, lesson)
        # Outline edits require If-Match; GETs ignore it.
        revision = await w.factory.course_revision(course.id)
        request: dict[str, Any] = {"headers": {"If-Match": str(revision)}}
        if body is not None:
            request["json"] = body
        return f"/api/v1/courses/{course.id}{path}", request

    return build


async def _version(w: World) -> Request:
    course, _, lesson = await _course(w)
    version = await w.factory.version(course, lesson)
    return f"/api/v1/courses/{course.id}/versions/{version.id}", {}


async def _assign(w: World) -> Request:
    course, _, _ = await _course(w, published=True)
    batch = await w.factory.batch(w.org)
    return f"/api/v1/courses/{course.id}/assignments", {"json": {"batch_ids": [str(batch.id)]}}


async def _assignment(w: World) -> Request:
    course, _, _ = await _course(w, published=True)
    assignment = await w.factory.assignment(course, w.org, batch=await w.factory.batch(w.org))
    return f"/api/v1/course-assignments/{assignment.id}", {}


async def _upgrade(w: World) -> Request:
    course, _, lesson = await _course(w, published=True)
    await w.factory.version(course, lesson, major=2)
    return f"/api/v1/courses/{course.id}/enrollment-upgrades", {"json": {"to_major": 2}}


async def _enrollment(w: World) -> tuple[Any, Any]:
    """Org A's student enrolled (through a batch assignment) in a published course."""
    course, _, lesson = await _course(w, published=True)
    batch = await w.factory.batch(w.org)
    await w.factory.add_to_batch(batch, w.users["student"])
    await w.factory.assignment(course, w.org, batch=batch)
    return await w.factory.enrollment(course, w.users["student"], w.org), lesson


async def _enrollment_path(w: World) -> Request:
    enrollment, _ = await _enrollment(w)
    return f"/api/v1/enrollments/{enrollment.id}", {}


def _lesson_action(action: str) -> Builder:
    async def build(w: World) -> Request:
        enrollment, lesson = await _enrollment(w)
        return f"/api/v1/enrollments/{enrollment.id}/lessons/{lesson.id}/{action}", {}

    return build


async def _skill(w: World) -> Request:
    return f"/api/v1/skills/{(await w.factory.skill()).id}", {"json": {"description": "x"}}


def _skill_body(_: World) -> Awaitable[Request]:
    return _static("/api/v1/skills", json={"name": "S", "slug": f"s_{uuid7().hex[-10:]}"})


def _course_body(_: World) -> Awaitable[Request]:
    return _static("/api/v1/courses", json={"title": f"Course {uuid7().hex[-10:]}"})


def _lesson_order(course: Any, module: Any, lesson: Any) -> dict[str, list[str]]:
    del course, module
    return {"ids": [str(lesson.id)]}


def _module_order(course: Any, module: Any, lesson: Any) -> dict[str, list[str]]:
    del course, lesson
    return {"ids": [str(module.id)]}


async def _assignment_draft(w: World, method: str) -> Request:
    course = await w.factory.course(w.org)
    lesson = await w.factory.lesson(await w.factory.module(course), lesson_type="assignment")
    path = f"/api/v1/courses/{course.id}/lessons/{lesson.id}/assignment"
    if method == "GET":
        return path, {}
    revision = await w.factory.course_revision(course.id)
    body = {"title": "Homework", "max_marks": 10, "submission_kinds": ["text"]}
    return path, {"json": body, "headers": {"If-Match": str(revision)}}


async def _homework(w: World) -> tuple[Any, Any, Any, Any, Any]:
    """Org A's student enrolled in a published course whose lesson is an assignment."""
    course = await w.factory.course(w.org)
    lesson = await w.factory.lesson(await w.factory.module(course), lesson_type="assignment")
    version, assignment_id = await w.factory.homework_version(course, lesson)
    batch = await w.factory.batch(w.org)
    await w.factory.add_to_batch(batch, w.users["student"])
    await w.factory.assignment(course, w.org, batch=batch)
    enrollment = await w.factory.enrollment(course, w.users["student"], w.org)
    return course, lesson, version, enrollment, assignment_id


def _my_homework(action: str) -> Builder:
    async def build(w: World) -> Request:
        _, lesson, _, enrollment, _ = await _homework(w)
        path = f"/api/v1/enrollments/{enrollment.id}/lessons/{lesson.id}/{action}"
        if action == "submission-upload":
            return path, {"json": {"file_name": "a.pdf", "content_type": "application/pdf"}}
        if action == "submission":
            body = {"submission": {"kind": "text", "text": "answer"}}
            return path, {"json": body, "headers": {"If-Match": "0"}}
        return path, {}

    return build


async def _submissions_queue(w: World) -> Request:
    course, lesson, _, _, _ = await _homework(w)
    return f"/api/v1/courses/{course.id}/lessons/{lesson.id}/submissions", {}


def _graded(action: str) -> Builder:
    async def build(w: World) -> Request:
        _, lesson, version, enrollment, assignment_id = await _homework(w)
        submission = await w.factory.submission(enrollment, lesson, version, assignment_id)
        path = f"/api/v1/assignment-submissions/{submission.id}"
        if action == "grade":
            return f"{path}/grade", {"json": {"score": "5"}, "headers": {"If-Match": "1"}}
        return path, {}

    return build


def _report(suffix: str) -> Builder:
    async def build(w: World) -> Request:
        course, _, _ = await _course(w, published=True)
        batch = await w.factory.batch(w.org)
        await w.factory.add_to_batch(batch, w.users["student"])
        await w.factory.assignment(course, w.org, batch=batch)
        if suffix == "batch-courses":
            return f"/api/v1/batches/{batch.id}/courses", {}
        return f"/api/v1/courses/{course.id}/{suffix}", {"params": {"batch_id": str(batch.id)}}

    return build


@dataclass(frozen=True)
class Route:
    method: str
    template: str  # the route's path template (matched against the app's routes)
    allowed: set[str]
    build: Builder
    names_resource: bool = False  # True: org B's admin gets 404 instead of 2xx
    denied: str = "403"  # status for signed-in callers outside `allowed`
    public: bool = False  # True: everyone, signed in or not, gets 2xx


def _content(kind: str) -> Builder:
    async def build(w: World) -> Request:
        return await _version_content(w, kind)  # defined after the matrix

    return build


MATRIX = [
    # Org A is not a content publisher: only platform admins may browse (publisher staff are
    # covered in identity/tests/test_organization_directory.py).
    Route(
        "GET", "/api/v1/organizations/directory", PLATFORM, at("/api/v1/organizations/directory")
    ),
    Route("GET", "/api/v1/catalog", set(ROLES), at("/api/v1/catalog"), public=True),
    Route(
        "GET",
        "/api/v1/catalog/{slug}",
        set(ROLES),
        lambda w: _catalog_path(w),  # noqa: PLW0108 - builder defined after the matrix
        public=True,
    ),
    Route(
        "POST",
        "/api/v1/files",
        STAFF_READ,
        at(
            "/api/v1/files",
            json={"kind": "pdf", "file_name": "a.pdf", "content_type": "application/pdf"},
        ),
    ),
    Route("GET", "/api/v1/files", STAFF_READ, at("/api/v1/files")),
    Route(
        "GET",
        "/api/v1/files/{file_id}",
        STAFF_READ,
        lambda w: _file_path(w),  # noqa: PLW0108 - builder defined after the matrix
        names_resource=True,
    ),
    Route(
        "POST",
        "/api/v1/files/{file_id}/confirm",
        STAFF_READ,
        lambda w: _file_path(w, "/confirm"),  # an already-confirmed file: idempotent 200
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/files/{file_id}/download",
        STAFF_READ,
        lambda w: _file_path(w, "/download"),
        names_resource=True,
    ),
    Route(
        "POST",
        "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/pdf-access",
        {"student"},
        lambda w: _file_action(w, "pdf"),
        denied="404",
    ),
    Route(
        "GET",
        "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/images",
        {"student"},
        lambda w: _file_action(w, "notes"),
        denied="404",
    ),
    Route("POST", "/api/v1/videos", STAFF_READ, at("/api/v1/videos", json={"title": "Video"})),
    Route("GET", "/api/v1/videos", STAFF_READ, at("/api/v1/videos")),
    Route(
        "GET",
        "/api/v1/videos/{video_id}",
        STAFF_READ,
        lambda w: _video_path(w),  # noqa: PLW0108 - builder defined after the matrix
        names_resource=True,
    ),
    Route(
        "POST",
        "/api/v1/videos/{video_id}/uploaded",
        STAFF_READ,
        lambda w: _video_path(w, "/uploaded"),
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/videos/{video_id}/playback",
        STAFF_READ,
        lambda w: _video_path(w, "/playback"),
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/playback",
        {"student"},
        lambda w: _video_action(w, "playback"),
        denied="404",
    ),
    Route(
        "GET",
        "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/resume",
        {"student"},
        lambda w: _video_action(w, "resume"),
        denied="404",
    ),
    Route(
        "POST",
        "/api/v1/progress/heartbeat",
        {"student"},
        lambda w: _video_action(w, "heartbeat"),
        denied="404",
    ),
    Route("GET", "/api/v1/me", EVERYONE, at("/api/v1/me")),
    # --- Organizations: platform admins only.
    Route("POST", "/api/v1/organizations", PLATFORM, _org_body),
    Route("GET", "/api/v1/organizations", PLATFORM, at("/api/v1/organizations?q=org")),
    Route("GET", "/api/v1/organizations/current", EVERYONE, at("/api/v1/organizations/current")),
    Route("GET", "/api/v1/organizations/{organization_id}", PLATFORM, _new_org),
    Route(
        "PATCH",
        "/api/v1/organizations/{organization_id}",
        PLATFORM,
        lambda w: _with_json(_new_org(w), {"name": "Renamed"}),
    ),
    Route("DELETE", "/api/v1/organizations/{organization_id}", PLATFORM, _new_org),
    # --- batches
    Route("POST", "/api/v1/batches", ADMINS, _batch_body),
    Route("GET", "/api/v1/batches", STAFF_READ, at("/api/v1/batches")),
    Route("GET", "/api/v1/batches/{batch_id}", STAFF_READ, _batch, names_resource=True),
    Route(
        "PATCH",
        "/api/v1/batches/{batch_id}",
        ADMINS,
        lambda w: _with_json(_batch(w), {"description": "x"}),
        names_resource=True,
    ),
    Route("DELETE", "/api/v1/batches/{batch_id}", ADMINS, _batch, names_resource=True),
    Route(
        "GET", "/api/v1/batches/{batch_id}/members", STAFF_READ, _batch_members, names_resource=True
    ),
    Route(
        "POST", "/api/v1/batches/{batch_id}/members", ADMINS, _add_batch_member, names_resource=True
    ),
    Route(
        "DELETE",
        "/api/v1/batches/{batch_id}/members/{user_id}",
        ADMINS,
        _remove_batch_member,
        names_resource=True,
    ),
    # --- members
    Route("GET", "/api/v1/members", STAFF_READ, at("/api/v1/members")),
    Route("GET", "/api/v1/members/{user_id}", STAFF_READ, _member, names_resource=True),
    Route("PATCH", "/api/v1/members/{user_id}", ADMINS, _patch_member, names_resource=True),
    Route("DELETE", "/api/v1/members/{user_id}", ADMINS, _member, names_resource=True),
    # --- invitations
    Route("POST", "/api/v1/invitations", ADMINS, _invite_body),
    Route("GET", "/api/v1/invitations", ADMINS, at("/api/v1/invitations")),
    Route(
        "DELETE", "/api/v1/invitations/{invitation_id}", ADMINS, _invitation, names_resource=True
    ),
    Route(
        "POST", "/api/v1/invitations/{invitation_id}/resend", ADMINS, _resend, names_resource=True
    ),
    # --- CSV imports.
    Route("POST", "/api/v1/imports", ADMINS, _import_body),
    Route("GET", "/api/v1/imports", ADMINS, at("/api/v1/imports")),
    Route("GET", "/api/v1/imports/{job_id}", ADMINS, _import, names_resource=True),
    Route(
        "GET", "/api/v1/imports/{job_id}/errors.csv", ADMINS, _import_errors, names_resource=True
    ),
    # --- audit
    Route("GET", "/api/v1/audit-log", ADMINS, at("/api/v1/audit-log")),
    # --- platform admin (Phase 2.5): platform admins only, whatever org is active.
    Route("GET", "/api/v1/platform/summary", PLATFORM, at("/api/v1/platform/summary")),
    Route("GET", "/api/v1/platform/users", PLATFORM, at("/api/v1/platform/users")),
    Route("GET", "/api/v1/platform/users/{user_id}", PLATFORM, lambda w: _platform_user(w, "")),
    Route(
        "POST",
        "/api/v1/platform/users/{user_id}/disable",
        PLATFORM,
        lambda w: _platform_user(w, "disable"),
    ),
    Route(
        "POST",
        "/api/v1/platform/users/{user_id}/enable",
        PLATFORM,
        lambda w: _platform_user(w, "enable"),
    ),
    Route(
        "POST",
        "/api/v1/platform/organizations/{organization_id}/admins",
        PLATFORM,
        lambda w: _org_admin_invite(w),  # noqa: PLW0108 - builder defined after the matrix
    ),
    Route("GET", "/api/v1/platform/courses", PLATFORM, at("/api/v1/platform/courses")),
    Route("GET", "/api/v1/platform/audit-log", PLATFORM, at("/api/v1/platform/audit-log")),
    # --- progress reports (Phase 2.5): the org's staff, for the org's own batches.
    Route(
        "GET",
        "/api/v1/courses/{course_id}/progress",
        STAFF_READ,
        _report("progress"),
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/courses/{course_id}/progress.csv",
        STAFF_READ,
        _report("progress.csv"),
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/batches/{batch_id}/courses",
        STAFF_READ,
        _report("batch-courses"),
        names_resource=True,
    ),
    # --- assignments (Phase 2.5): owner-org editors define them; the enrolled student submits
    # (everyone else 404); graders are instructors and org admins of the student's org.
    Route(
        "GET",
        "/api/v1/courses/{course_id}/lessons/{lesson_id}/assignment",
        STAFF_READ,
        lambda w: _assignment_draft(w, "GET"),
        names_resource=True,
    ),
    Route(
        "PUT",
        "/api/v1/courses/{course_id}/lessons/{lesson_id}/assignment",
        STAFF_READ,
        lambda w: _assignment_draft(w, "PUT"),
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/assignment",
        {"student"},
        _my_homework("assignment"),
        denied="404",
    ),
    Route(
        "POST",
        "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/submission-upload",
        {"student"},
        _my_homework("submission-upload"),
        denied="404",
    ),
    Route(
        "PUT",
        "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/submission",
        {"student"},
        _my_homework("submission"),
        denied="404",
    ),
    Route(
        "GET",
        "/api/v1/courses/{course_id}/lessons/{lesson_id}/submissions",
        STAFF_READ,
        lambda w: _submissions_queue(w),  # noqa: PLW0108 - builder defined after the matrix
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/assignment-submissions/{submission_id}",
        STAFF_READ,
        _graded("detail"),
        names_resource=True,
    ),
    Route(
        "PUT",
        "/api/v1/assignment-submissions/{submission_id}/grade",
        STAFF_READ,
        _graded("grade"),
        names_resource=True,
    ),
    # --- skills: everyone reads; org A isn't a content publisher, so only platform admins
    # write.
    Route("GET", "/api/v1/skills", EVERYONE, at("/api/v1/skills")),
    Route("POST", "/api/v1/skills", PLATFORM, _skill_body),
    Route("PATCH", "/api/v1/skills/{skill_id}", PLATFORM, _skill),
    # --- courses: org A's instructors and org admins author; org B's admin can't see them.
    Route("POST", "/api/v1/courses", STAFF_READ, _course_body),
    Route("GET", "/api/v1/courses", STAFF_READ, at("/api/v1/courses")),
    Route(
        "GET",
        "/api/v1/courses/{course_id}",
        STAFF_READ,
        _course_path,
        names_resource=True,
    ),
    Route(
        "PATCH",
        "/api/v1/courses/{course_id}",
        STAFF_READ,
        lambda w: _with_json(_course_path(w), {"description": "x"}),
        names_resource=True,
    ),
    Route(
        "DELETE",
        "/api/v1/courses/{course_id}",
        STAFF_READ,
        _course_path,
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/courses/{course_id}/draft",
        STAFF_READ,
        _course_sub("/draft"),
        names_resource=True,
    ),
    Route(
        "POST",
        "/api/v1/courses/{course_id}/modules",
        STAFF_READ,
        _course_sub("/modules", json={"title": "M"}),
        names_resource=True,
    ),
    Route(
        "PUT",
        "/api/v1/courses/{course_id}/modules/order",
        STAFF_READ,
        _course_sub("/modules/order", json=_module_order),
        names_resource=True,
    ),
    Route(
        "PATCH",
        "/api/v1/courses/{course_id}/modules/{module_id}",
        STAFF_READ,
        _course_sub("/modules/{m}", json={"title": "N"}),
        names_resource=True,
    ),
    Route(
        "DELETE",
        "/api/v1/courses/{course_id}/modules/{module_id}",
        STAFF_READ,
        _course_sub("/modules/{m}"),
        names_resource=True,
    ),
    Route(
        "POST",
        "/api/v1/courses/{course_id}/modules/{module_id}/lessons",
        STAFF_READ,
        _course_sub("/modules/{m}/lessons", json={"title": "L", "lesson_type": "notes"}),
        names_resource=True,
    ),
    Route(
        "PUT",
        "/api/v1/courses/{course_id}/modules/{module_id}/lessons/order",
        STAFF_READ,
        _course_sub("/modules/{m}/lessons/order", json=_lesson_order),
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/courses/{course_id}/lessons/{lesson_id}/preview",
        STAFF_READ,
        _course_sub("/lessons/{lesson}/preview"),
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/courses/{course_id}/lessons/{lesson_id}",
        STAFF_READ,
        _course_sub("/lessons/{lesson}"),
        names_resource=True,
    ),
    Route(
        "PATCH",
        "/api/v1/courses/{course_id}/lessons/{lesson_id}",
        STAFF_READ,
        _course_sub("/lessons/{lesson}", json={"title": "L2"}),
        names_resource=True,
    ),
    Route(
        "DELETE",
        "/api/v1/courses/{course_id}/lessons/{lesson_id}",
        STAFF_READ,
        _course_sub("/lessons/{lesson}"),
        names_resource=True,
    ),
    Route(
        "PUT",
        "/api/v1/courses/{course_id}/lessons/{lesson_id}/skills",
        STAFF_READ,
        _course_sub("/lessons/{lesson}/skills", json={"skill_ids": []}),
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/courses/{course_id}/publish-preview",
        STAFF_READ,
        _course_sub("/publish-preview"),
        names_resource=True,
    ),
    Route(
        "POST",
        "/api/v1/courses/{course_id}/versions",
        STAFF_READ,
        _course_sub("/versions", json={"release_type": "major"}),
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/courses/{course_id}/versions",
        STAFF_READ,
        _course_sub("/versions"),
        names_resource=True,
    ),
    Route(
        "GET",
        "/api/v1/courses/{course_id}/versions/{version_id}",
        STAFF_READ,
        _version,
        names_resource=True,
    ),
    *(
        Route(
            "GET",
            f"/api/v1/courses/{{course_id}}/versions/{{version_id}}/lessons/{{lesson_id}}/{kind}",
            STAFF_READ,
            _content(kind),
            names_resource=True,
        )
        for kind in ("playback", "pdf", "images")
    ),
    # --- assignments
    Route(
        "GET",
        "/api/v1/courses/{course_id}/assignments",
        STAFF_READ,
        _course_sub("/assignments", published=True),
        names_resource=True,
    ),
    Route(
        "POST",
        "/api/v1/courses/{course_id}/assignments",
        STAFF_READ,
        _assign,
        names_resource=True,
    ),
    Route(
        "DELETE",
        "/api/v1/course-assignments/{assignment_id}",
        STAFF_READ,
        _assignment,
        names_resource=True,
    ),
    # --- enrollments: a student's own; anyone else gets 404 (existence isn't revealed).
    Route("GET", "/api/v1/enrollments", EVERYONE, at("/api/v1/enrollments")),
    Route(
        "GET", "/api/v1/enrollments/{enrollment_id}", {"student"}, _enrollment_path, denied="404"
    ),
    Route(
        "POST",
        "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/visit",
        {"student"},
        _lesson_action("visit"),
        denied="404",
    ),
    Route(
        "POST",
        "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/complete",
        {"student"},
        _lesson_action("complete"),
        denied="404",
    ),
    Route(
        "POST",
        "/api/v1/courses/{course_id}/enrollment-upgrades",
        ADMINS,
        _upgrade,
        names_resource=True,
    ),
]


async def _platform_user(w: World, action: str) -> Request:
    user = await w.factory.member(w.org, "student")
    return f"/api/v1/platform/users/{user.id}{f'/{action}' if action else ''}", {}


async def _org_admin_invite(w: World) -> Request:
    org = await w.factory.org()
    body = {"email": f"admin-{uuid7().hex[-8:]}@college.test", "full_name": "First Admin"}
    return f"/api/v1/platform/organizations/{org.id}/admins", {"json": body}


async def _catalog_path(w: World) -> Request:
    course, _, lesson = await _course(w)
    entry = await w.factory.catalog_entry(course, await w.factory.version(course, lesson))
    return f"/api/v1/catalog/{entry.slug}", {}


async def _version_content(w: World, kind: str) -> Request:
    """A published version of an org A course whose lesson uses a video, a PDF or an image."""
    lesson_type = {"playback": "video", "pdf": "pdf", "images": "notes"}[kind]
    course = await w.factory.course(w.org)
    module = await w.factory.module(course)
    lesson = await w.factory.lesson(module, lesson_type=lesson_type)
    if kind == "playback":
        video = await w.factory.video(w.org, status="ready", duration=120)
        version = await w.factory.version(course, lesson, video=video)
    else:
        file = await (w.factory.pdf(w.org) if kind == "pdf" else w.factory.image(w.org))
        version = await w.factory.version(course, lesson, files=[file])
    return f"/api/v1/courses/{course.id}/versions/{version.id}/lessons/{lesson.id}/{kind}", {}


async def _file_path(w: World, suffix: str = "") -> Request:
    file = await w.factory.pdf(w.org)
    return f"/api/v1/files/{file.id}{suffix}", {}


async def _file_action(w: World, lesson_type: str) -> Request:
    """A student enrolled in a version whose pdf (or notes) lesson uses a file."""
    course = await w.factory.course(w.org)
    module = await w.factory.module(course)
    lesson = await w.factory.lesson(module, lesson_type=lesson_type)
    file = await (w.factory.pdf(w.org) if lesson_type == "pdf" else w.factory.image(w.org))
    await w.factory.version(course, lesson, files=[file])
    batch = await w.factory.batch(w.org)
    await w.factory.add_to_batch(batch, w.users["student"])
    await w.factory.assignment(course, w.org, batch=batch)
    enrollment = await w.factory.enrollment(course, w.users["student"], w.org)
    action = "pdf-access" if lesson_type == "pdf" else "images"
    return f"/api/v1/enrollments/{enrollment.id}/lessons/{lesson.id}/{action}", {}


async def _video_path(w: World, suffix: str = "") -> Request:
    video = await w.factory.video(w.org, status="ready", duration=120)
    return f"/api/v1/videos/{video.id}{suffix}", {}


async def _video_action(w: World, action: str) -> Request:
    course = await w.factory.course(w.org)
    module = await w.factory.module(course)
    lesson = await w.factory.lesson(module, lesson_type="video")
    video = await w.factory.video(w.org, status="ready", duration=120)
    await w.factory.version(course, lesson, video=video)
    batch = await w.factory.batch(w.org)
    await w.factory.add_to_batch(batch, w.users["student"])
    await w.factory.assignment(course, w.org, batch=batch)
    enrollment = await w.factory.enrollment(course, w.users["student"], w.org)
    if action == "heartbeat":
        return "/api/v1/progress/heartbeat", {
            "json": {
                "enrollment_id": str(enrollment.id),
                "lesson_id": str(lesson.id),
                "video_asset_id": str(video.id),
                "position_seconds": 15,
                "played_seconds": 15,
            }
        }
    return f"/api/v1/enrollments/{enrollment.id}/lessons/{lesson.id}/{action}", {}


async def _with_json(request: Awaitable[Request], body: dict[str, Any]) -> Request:
    path, kwargs = await request
    return path, {**kwargs, "json": body}


async def _assessment(w: World, resource: str, suffix: str = "", **kwargs: Any) -> Request:
    bank = QuestionBank(id=new_id(), organization_id=w.org.id, name="Bank")
    await w.factory._save(bank)
    question = Question(
        id=new_id(),
        organization_id=w.org.id,
        bank_id=bank.id,
        question_type="mcq_single",
        prompt="Pick",
        options=[{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
    )
    await w.factory._save(question)
    await w.factory._save(
        QuestionKey(
            question_id=question.id,
            organization_id=w.org.id,
            answer_key={"correct_option_ids": ["a"]},
        )
    )
    headers = {"If-Match": "1"}
    if resource == "bank":
        return f"/api/v1/question-banks/{bank.id}{suffix}", {"headers": headers, **kwargs}
    if resource == "question":
        return f"/api/v1/questions/{question.id}{suffix}", {"headers": headers, **kwargs}
    course = await w.factory.course(w.org)
    module = await w.factory.module(course)
    lesson = await w.factory.lesson(module, lesson_type="quiz")
    values = {
        "selection_mode": "manual",
        "selection": {
            "mode": "manual",
            "questions": [{"question_id": str(question.id), "marks": "2"}],
        },
        "max_marks": 2,
        "pass_marks": 1,
        "time_limit_seconds": 60,
        "attempts_allowed": 2,
        "title": "Quiz",
    }
    quiz = Quiz(
        id=new_id(), organization_id=w.org.id, course_id=course.id, lesson_id=lesson.id, **values
    )
    await w.factory._save(quiz)
    if resource == "published":
        version = await w.factory.version(course, lesson)
        published = QuizVersion(
            id=new_id(),
            organization_id=w.org.id,
            course_id=course.id,
            lesson_id=lesson.id,
            quiz_id=quiz.id,
            course_version_id=version.id,
            major_version=1,
            **values,
        )
        await w.factory._save(published)
        await w.factory._save(
            QuizVersionQuestion(
                id=new_id(),
                organization_id=w.org.id,
                quiz_version_id=published.id,
                question_id=question.id,
                question_type=question.question_type,
                prompt=question.prompt,
                options=question.options,
                marks=2,
                position=0,
            )
        )
        return f"/api/v1/courses/{course.id}/versions/{version.id}/lessons/{lesson.id}/quiz", {}
    if "json" in kwargs:
        kwargs["json"] = {
            k: v for k, v in values.items() if k not in {"selection_mode", "max_marks"}
        }
    return f"/api/v1/courses/{course.id}/lessons/{lesson.id}/quiz", {"headers": headers, **kwargs}


_QUESTION_BODY = {
    "question_type": "mcq_single",
    "prompt": "Pick",
    "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
    "answer_key": {"correct_option_ids": ["a"]},
}
MATRIX.extend(
    [
        Route("GET", "/api/v1/question-banks", STAFF_READ, at("/api/v1/question-banks")),
        Route(
            "POST",
            "/api/v1/question-banks",
            STAFF_READ,
            at("/api/v1/question-banks", json={"name": "Bank"}, headers={"If-Match": "0"}),
        ),
        Route(
            "GET",
            "/api/v1/question-banks/{bank_id}",
            STAFF_READ,
            lambda w: _assessment(w, "bank"),
            names_resource=True,
        ),
        Route(
            "PATCH",
            "/api/v1/question-banks/{bank_id}",
            STAFF_READ,
            lambda w: _assessment(w, "bank", json={"name": "Changed"}),
            names_resource=True,
        ),
        Route(
            "DELETE",
            "/api/v1/question-banks/{bank_id}",
            STAFF_READ,
            lambda w: _assessment(w, "bank"),
            names_resource=True,
        ),
        Route(
            "GET",
            "/api/v1/question-banks/{bank_id}/questions",
            STAFF_READ,
            lambda w: _assessment(w, "bank", "/questions"),
            names_resource=True,
        ),
        Route(
            "POST",
            "/api/v1/question-banks/{bank_id}/questions",
            STAFF_READ,
            lambda w: _assessment(w, "bank", "/questions", json=_QUESTION_BODY),
            names_resource=True,
        ),
        Route(
            "GET",
            "/api/v1/questions/{question_id}",
            STAFF_READ,
            lambda w: _assessment(w, "question"),
            names_resource=True,
        ),
        Route(
            "PATCH",
            "/api/v1/questions/{question_id}",
            STAFF_READ,
            lambda w: _assessment(w, "question", json={"prompt": "Changed"}),
            names_resource=True,
        ),
        Route(
            "DELETE",
            "/api/v1/questions/{question_id}",
            STAFF_READ,
            lambda w: _assessment(w, "question"),
            names_resource=True,
        ),
        Route(
            "PUT",
            "/api/v1/questions/{question_id}/skills",
            STAFF_READ,
            lambda w: _assessment(w, "question", "/skills", json={"skill_ids": []}),
            names_resource=True,
        ),
        Route(
            "GET",
            "/api/v1/courses/{course_id}/lessons/{lesson_id}/quiz",
            STAFF_READ,
            lambda w: _assessment(w, "quiz"),
            names_resource=True,
        ),
        Route(
            "PUT",
            "/api/v1/courses/{course_id}/lessons/{lesson_id}/quiz",
            STAFF_READ,
            lambda w: _assessment(w, "quiz", json={}),
            names_resource=True,
        ),
        Route(
            "GET",
            "/api/v1/courses/{course_id}/versions/{version_id}/lessons/{lesson_id}/quiz",
            STAFF_READ,
            lambda w: _assessment(w, "published"),
            names_resource=True,
        ),
    ]
)


def expected_status(route: Route, role: str) -> str:
    if route.public:
        return "2xx"
    if role == "anonymous":
        return "401"
    if role not in route.allowed:
        return route.denied
    if role == "other_admin" and route.names_resource:
        return "404"
    return "2xx"


@pytest.fixture
async def world(factory: Factory) -> World:
    org, other_org = await factory.org(), await factory.org()
    users = {
        "student": await factory.member(org, "student"),
        "lab_author": await factory.member(org, "lab_author"),
        "instructor": await factory.member(org, "instructor"),
        "org_admin": await factory.member(org, "org_admin"),
        "other_admin": await factory.member(other_org, "org_admin"),
        "platform_admin": await factory.user(),
    }
    return World(factory=factory, org=org, users=users, other_org=other_org)


def _headers(world: World, role: str, auth_headers: AuthHeaders) -> dict[str, str]:
    if role == "anonymous":
        return {}
    if role == "other_admin":
        return auth_headers(world.users[role], org=world.other_org.id)
    return auth_headers(
        world.users[role], org=world.org.id, platform_admin=role == "platform_admin"
    )


@pytest.mark.usefixtures("fake_idp", "enqueued")
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("route", MATRIX, ids=[f"{r.method} {r.template}" for r in MATRIX])
async def test_role_matrix(
    client: AsyncClient, world: World, auth_headers: AuthHeaders, route: Route, role: str
) -> None:
    path, kwargs = await route.build(world)
    headers = {**_headers(world, role, auth_headers), **kwargs.pop("headers", {})}
    response = await client.request(route.method, path, headers=headers, **kwargs)
    expected = expected_status(route, role)
    actual = "2xx" if 200 <= response.status_code < 300 else str(response.status_code)
    assert actual == expected, (route.method, path, role, response.text[:300])


def test_every_route_is_in_the_matrix(app: FastAPI) -> None:
    declared = {(r.method, r.template) for r in MATRIX}
    # From the OpenAPI spec: the public API surface (FastAPI includes routers lazily, so
    # app.routes does not list them).
    actual = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1")
        for method in operations
    }
    assert actual - declared == set(), "add these endpoints to MATRIX with their allowed roles"
    assert declared - actual == set(), "MATRIX lists endpoints that no longer exist"


# Silence "unused" for fixtures only used via usefixtures.
_ = (FakeKeycloakAdmin, EnqueueRecorder)
