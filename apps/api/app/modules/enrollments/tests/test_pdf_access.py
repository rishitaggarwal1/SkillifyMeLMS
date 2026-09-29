"""PDF lessons for students: signed downloads, "opened" before completion, and file RLS."""

from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from sqlalchemy import select

from app.modules.media.models import StoredFile
from tests.course_api import Campus, CourseApi, ok, published_for_cse
from tests.fixtures import TenantSessionFactory


async def pdf_setup(api: CourseApi, campus: Campus) -> tuple[str, UUID, UUID, UUID]:
    """A published pdf + notes course for CSE. Returns (enrollment id, pdf lesson, notes lesson,
    course id)."""
    course = await published_for_cse(api, campus, modules=(("pdf", "notes"),))
    enrollment = await api.enrollment_for(campus.cse, campus.c, course.id)
    assert enrollment
    pdf_lesson, notes_lesson = course.lesson_ids
    return enrollment["id"], pdf_lesson, notes_lesson, course.id


def path(enrollment: str, lesson: UUID, action: str) -> str:
    return f"/enrollments/{enrollment}/lessons/{lesson}/{action}"


async def progress_of(api: CourseApi, campus: Campus, enrollment: str, lesson: UUID) -> Any:
    detail = ok(await api.request("GET", f"/enrollments/{enrollment}", campus.cse, campus.c))
    return next((p for p in detail["progress"] if p["lesson_id"] == str(lesson)), None)


async def test_pdf_must_be_opened_before_completion(api: CourseApi, campus: Campus) -> None:
    enrollment, pdf_lesson, _, _ = await pdf_setup(api, campus)

    early = await api.request(
        "POST", path(enrollment, pdf_lesson, "complete"), campus.cse, campus.c
    )
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "pdf_not_opened"

    opened = ok(
        await api.request("POST", path(enrollment, pdf_lesson, "pdf-access"), campus.cse, campus.c)
    )
    assert parse_qs(urlsplit(opened["url"]).query)["X-Amz-Expires"] == ["300"]
    assert opened["file_name"] == "notes.pdf"
    first = (await progress_of(api, campus, enrollment, pdf_lesson))["pdf_opened_at"]
    assert first
    ok(await api.request("POST", path(enrollment, pdf_lesson, "pdf-access"), campus.cse, campus.c))
    assert (await progress_of(api, campus, enrollment, pdf_lesson))["pdf_opened_at"] == first

    done = ok(
        await api.request("POST", path(enrollment, pdf_lesson, "complete"), campus.cse, campus.c)
    )
    assert done["lesson"]["status"] == "completed"
    assert done["enrollment"]["progress_percent"] == 50  # one of two required lessons


async def test_pdf_access_is_limited_to_the_enrolled_student(
    api: CourseApi, campus: Campus
) -> None:
    enrollment, pdf_lesson, notes_lesson, _ = await pdf_setup(api, campus)
    for user, org in [
        (campus.ece, campus.c),
        (campus.c_admin, campus.c),
        (campus.o_admin, campus.o),
    ]:
        response = await api.request("POST", path(enrollment, pdf_lesson, "pdf-access"), user, org)
        assert response.status_code == 404
    not_pdf = await api.request(
        "POST", path(enrollment, notes_lesson, "pdf-access"), campus.cse, campus.c
    )
    assert not_pdf.status_code == 404


async def test_file_rls_follows_enrollment_and_assignment(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    enrollment, pdf_lesson, _, course_id = await pdf_setup(api, campus)
    lesson = ok(await api.request("GET", f"/courses/{course_id}/lessons/{pdf_lesson}",
                                  campus.author, campus.p))  # fmt: skip
    file_id = UUID(lesson["content"]["file_id"])

    async def visible(user: Any, org: Any) -> bool:
        async with tenant_session(org=org.id, user=user.id) as session:
            found = await session.scalar(select(StoredFile.id).where(StoredFile.id == file_id))
            return found is not None

    assert await visible(campus.cse, campus.c)
    assert await visible(campus.author, campus.p)  # owner-org editor
    assert not await visible(campus.ece, campus.c)  # same org, other batch
    assert not await visible(campus.c_admin, campus.c)  # staff of an assigned org
    assert not await visible(campus.o_admin, campus.o)

    assignments = ok(
        await api.request("GET", f"/courses/{course_id}/assignments", campus.c_admin, campus.c)
    )
    batch_row = next(a for a in assignments["items"] if a["batch_id"] == str(campus.cse_batch.id))
    removed = await api.request(
        "DELETE", f"/course-assignments/{batch_row['id']}", campus.c_admin, campus.c
    )
    assert removed.is_success
    assert not await visible(campus.cse, campus.c)  # revoked immediately, before any job runs
    gone = await api.request(
        "POST", path(enrollment, pdf_lesson, "pdf-access"), campus.cse, campus.c
    )
    assert gone.status_code == 404


async def test_a_pdf_replaced_in_a_minor_release_is_the_one_students_get(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    enrollment, pdf_lesson, _, course_id = await pdf_setup(api, campus)
    old = ok(await api.request("GET", f"/courses/{course_id}/lessons/{pdf_lesson}",
                               campus.author, campus.p))["content"]["file_id"]  # fmt: skip
    replacement = await api.factory.pdf(campus.p)
    ok(
        await api.request(
            "PATCH", f"/courses/{course_id}/lessons/{pdf_lesson}", campus.author, campus.p,
            json={"content": {"file_id": str(replacement.id)}},
        )
    )  # fmt: skip
    ok(await api.publish(campus.author, campus.p, course_id, "minor"), 201)

    async with tenant_session(org=campus.c.id, user=campus.cse.id) as session:
        readable = set(
            await session.scalars(
                select(StoredFile.id).where(StoredFile.id.in_([UUID(old), replacement.id]))
            )
        )
    assert readable == {replacement.id}  # only the latest minor's file
    opened = ok(
        await api.request("POST", path(enrollment, pdf_lesson, "pdf-access"), campus.cse, campus.c)
    )
    assert urlsplit(opened["url"]).path.endswith(replacement.storage_key)
