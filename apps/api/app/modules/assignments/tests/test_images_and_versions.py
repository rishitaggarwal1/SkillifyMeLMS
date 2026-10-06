"""Assignment instruction image scope and minor/major frozen-rule boundaries."""

from uuid import UUID

import pytest
from sqlalchemy import text

from app.modules.assignments.tests.history_helpers import RUBRIC, AssignmentWorld, create_world
from app.modules.courses.tests.test_notes_api import notes_doc
from tests.course_api import Campus, CourseApi, ok
from tests.fixtures import TenantSessionFactory


async def test_images_are_confirmed_same_owner_only(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, modules=(("assignment",),))
    for image in [
        await api.factory.image(campus.c),
        await api.factory.image(campus.p, status="pending"),
        await api.factory.pdf(campus.p),
    ]:
        response = await api.define_assignment(
            campus.author,
            campus.p,
            course.id,
            course.lesson_ids[0],
            instructions=notes_doc(image=image.id),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_image"


async def test_published_and_historical_instruction_images_remain_narrow(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    image = await api.factory.image(campus.p)
    w = await create_world(api, campus, instructions=notes_doc(image=image.id))
    prefix = f"/courses/{w.course.id}/lessons/{w.lesson_id}/assignment"
    preview = ok(await api.request("GET", prefix + "/preview", campus.author, campus.p))
    assert str(image.id) in preview["image_urls"]
    assert "<script>" not in preview["html"]
    assert "data-file-id" in preview["html"]
    assert (
        await api.request("GET", prefix + "/preview", campus.c_instructor, campus.c)
    ).status_code == 404
    current = ok(await w.mine("GET", "images"))
    assert str(image.id) in current["urls"]
    assert current["expires_at"]
    submitted = await w.submit()
    async with api.factory.sessionmaker() as reader:
        version = await reader.scalar(
            text("SELECT version_id FROM assignment_submissions WHERE id=:id"),
            {"id": UUID(submitted["id"])},
        )
        assert await reader.scalar(
            text(
                "SELECT file_ids FROM course_version_lessons WHERE version_id=:v AND lesson_id=:l"
            ),
            {"v": version, "l": w.lesson_id},
        ) == [image.id]
    reader_url = f"/courses/{w.course.id}/versions/{version}/lessons/{w.lesson_id}/images"
    staff = ok(await api.request("GET", reader_url, campus.c_instructor, campus.c))
    assert str(image.id) in staff["urls"]
    # A minor removes the current image: only this student's frozen work keeps it.
    ok(
        await api.define_assignment(
            campus.author, campus.p, w.course.id, w.lesson_id, instructions=notes_doc()
        )
    )
    ok(await api.publish(campus.author, campus.p, w.course.id, "minor"), 201)
    assert ok(await w.mine("GET", "images"))["urls"] == {}
    history = ok(await w.mine("GET", "submission-attempts"))["items"][0]
    assert str(image.id) in history["image_urls"]
    assert history["image_urls_expires_at"]
    async with tenant_session(org=campus.c.id, user=campus.cse.id) as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM files WHERE id=:id"), {"id": image.id})
            == 1
        )
    async with tenant_session(org=campus.c.id, user=campus.ece.id) as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM files WHERE id=:id"), {"id": image.id})
            == 0
        )
    detail = ok(await api.request("GET", f"/courses/{w.course.id}", campus.c_admin, campus.c))
    assignment_rows = ok(
        await api.request("GET", f"/courses/{w.course.id}/assignments", campus.c_admin, campus.c)
    )
    batch = next(a for a in assignment_rows["items"] if a["batch_id"] == str(campus.cse_batch.id))
    ok(
        await api.request("DELETE", f"/course-assignments/{batch['id']}", campus.c_admin, campus.c),
        204,
    )
    async with tenant_session(org=campus.c.id, user=campus.cse.id) as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM files WHERE id=:id"), {"id": image.id})
            == 0
        )
    assert (await w.mine("GET", "submission-attempts")).status_code == 404
    assert detail["id"] == str(w.course.id)


@pytest.mark.parametrize("change", ["ids", "maxima"])
async def test_rubric_structural_changes_block_minor(
    api: CourseApi, campus: Campus, change: str
) -> None:
    w = await create_world(api, campus, rubric=RUBRIC)
    criteria = [dict(c) for c in RUBRIC["criteria"]]
    if change == "ids":
        criteria[0]["id"] = "replacement"
    else:
        criteria[0]["max_marks"] = "5"
        criteria[1]["max_marks"] = "5"
    ok(
        await api.define_assignment(
            campus.author, campus.p, w.course.id, w.lesson_id, rubric={"criteria": criteria}
        )
    )
    preview = ok(
        await api.request("GET", f"/courses/{w.course.id}/publish-preview", campus.author, campus.p)
    )
    assert "assignment_structure_changed" in [c["code"] for c in preview["structural_changes"]]
    assert not preview["minor_allowed"]
    assert (await api.publish(campus.author, campus.p, w.course.id, "minor")).status_code == 409
    ok(await api.publish(campus.author, campus.p, w.course.id, "major"), 201)


async def test_grading_old_major_work_cannot_create_new_completion(
    assignment_world: AssignmentWorld,
) -> None:
    w = assignment_world
    sub = await w.submit()
    ok(await w.api.publish(w.campus.author, w.campus.p, w.course.id, "major"), 201)
    ok(
        await w.api.request(
            "POST",
            f"/courses/{w.course.id}/enrollment-upgrades",
            w.campus.c_admin,
            w.campus.c,
            json={"to_major": 2},
        ),
        202,
    )
    await w.api.run_jobs()
    ok(await w.grade(sub["id"], 1, score="8"))
    detail = ok(
        await w.api.request("GET", f"/enrollments/{w.enrollment_id}", w.campus.cse, w.campus.c)
    )
    assert detail["enrollment"]["major_version"] == 2
    assert detail["enrollment"]["progress_percent"] == 0
    assert not any(p["status"] == "completed" for p in detail["progress"])
