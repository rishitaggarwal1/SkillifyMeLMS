"""Read access is to the content: staff of an assigned org open videos, PDFs and notes images.

Owner-org editors and the org_admins/instructors of an org the course is assigned to may play
and download the lesson content of published versions. Students only through their enrollment
(an assigned batch); unassigned orgs never. Checked through RLS and through the API.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.modules.courses.tests.test_notes_api import notes_doc, set_doc
from app.modules.media.models import StoredFile, VideoAsset
from tests.course_api import BuiltCourse, Campus, CourseApi, ok
from tests.fixtures import TenantSessionFactory


async def shared_course(
    api: CourseApi, campus: Campus, *, batch: bool = True
) -> tuple[BuiltCourse, str, dict[str, Any]]:
    """Publisher course with video, pdf and notes (with an image), granted to College and (by
    default) distributed to its CSE batch only. Returns the course, version id and the ids of
    the video asset, pdf file and image file."""
    image = await api.factory.image(campus.p)
    course = await api.build(campus.author, campus.p, [("video", "pdf", "notes")])
    ok(await set_doc(api, campus, course, course.lesson_ids[2], notes_doc(image=image.id)))
    version = ok(await api.publish(campus.author, campus.p, course.id), 201)
    ok(await api.assign(campus.author, campus.p, course.id, to=campus.c), 201)
    if batch:
        ok(await api.assign(campus.c_admin, campus.c, course.id, batches=[campus.cse_batch]), 201)
    await api.run_jobs()
    lessons = []
    for lid in course.lesson_ids[:2]:
        path = f"/courses/{course.id}/lessons/{lid}"
        lessons.append(ok(await api.request("GET", path, campus.author, campus.p))["content"])
    ids = {
        "video": UUID(lessons[0]["video_asset_id"]),
        "pdf": UUID(lessons[1]["file_id"]),
        "image": image.id,
    }
    return course, version["id"], ids


def content_paths(course: BuiltCourse, version: str) -> list[str]:
    video, pdf, notes = course.lesson_ids
    base = f"/courses/{course.id}/versions/{version}/lessons"
    return [f"{base}/{video}/playback", f"{base}/{pdf}/pdf", f"{base}/{notes}/images"]


async def rls_visible(
    tenant_session: TenantSessionFactory, user: Any, org: Any, ids: dict[str, Any]
) -> dict[str, bool]:
    async with tenant_session(org=org.id, user=user.id) as session:
        video = await session.scalar(select(VideoAsset.id).where(VideoAsset.id == ids["video"]))
        files = set(
            await session.scalars(
                select(StoredFile.id).where(StoredFile.id.in_([ids["pdf"], ids["image"]]))
            )
        )
    return {"video": video is not None, "pdf": ids["pdf"] in files, "image": ids["image"] in files}


async def test_assigned_org_staff_can_play_and_download(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    course, version, ids = await shared_course(api, campus)
    for user in (campus.c_instructor, campus.c_admin):
        assert await rls_visible(tenant_session, user, campus.c, ids) == dict.fromkeys(
            ("video", "pdf", "image"), True
        )
        playback, pdf, images = [
            ok(await api.request("GET", p, user, campus.c)) for p in content_paths(course, version)
        ]
        assert playback["kind"] == "mp4"
        assert pdf["file_name"] == "notes.pdf"
        assert list(images["urls"]) == [str(ids["image"])]


async def test_an_org_grant_alone_gives_staff_the_content(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    course, version, ids = await shared_course(api, campus, batch=False)
    assert all((await rls_visible(tenant_session, campus.c_instructor, campus.c, ids)).values())
    for path in content_paths(course, version):
        ok(await api.request("GET", path, campus.c_instructor, campus.c))
    # ...but no student of that org sees anything.
    assert not any((await rls_visible(tenant_session, campus.cse, campus.c, ids)).values())


async def test_assigned_org_student_outside_the_batch_cannot(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    course, version, ids = await shared_course(api, campus)
    assert all((await rls_visible(tenant_session, campus.cse, campus.c, ids)).values())
    assert not any((await rls_visible(tenant_session, campus.ece, campus.c, ids)).values())
    for path in content_paths(course, version):
        # Students have no course.read: staff routes are 403 whatever the course.
        assert (await api.request("GET", path, campus.ece, campus.c)).status_code == 403
    assert await api.enrollment_for(campus.ece, campus.c, course.id) is None


async def test_unassigned_org_cannot(
    api: CourseApi, campus: Campus, tenant_session: TenantSessionFactory
) -> None:
    course, version, ids = await shared_course(api, campus)
    assert not any((await rls_visible(tenant_session, campus.o_admin, campus.o, ids)).values())
    for path in content_paths(course, version):
        assert (await api.request("GET", path, campus.o_admin, campus.o)).status_code == 404


async def test_staff_read_older_versions_but_editing_stays_owner_only(
    api: CourseApi, campus: Campus
) -> None:
    course, v1, _ = await shared_course(api, campus)
    replacement = await api.factory.pdf(campus.p)
    ok(
        await api.request(
            "PATCH", f"/courses/{course.id}/lessons/{course.lesson_ids[1]}", campus.author,
            campus.p, json={"content": {"file_id": str(replacement.id)}},
        )
    )  # fmt: skip
    ok(await api.publish(campus.author, campus.p, course.id, "minor"), 201)
    # 1.0's PDF is still readable to staff (they can read every published version).
    ok(await api.request("GET", content_paths(course, v1)[1], campus.c_instructor, campus.c))
    # Read access never becomes edit access.
    for method, path, body in [
        ("PATCH", f"/courses/{course.id}/lessons/{course.lesson_ids[1]}", {"title": "X"}),
        ("GET", f"/courses/{course.id}/lessons/{course.lesson_ids[2]}/preview", None),
        ("GET", f"/files/{replacement.id}/download", None),
    ]:
        response = await api.request(
            method, path, campus.c_instructor, campus.c, json=body,
            headers={"If-Match": "1"} if method == "PATCH" else {},
        )  # fmt: skip
        assert response.status_code == 404, (method, path)
