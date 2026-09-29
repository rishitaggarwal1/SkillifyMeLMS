"""Notes lessons through the API: validation, image checks, publish-time rendering, previews."""

from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from tests.course_api import BuiltCourse, Campus, CourseApi, ok, published_for_cse


def notes_doc(*, image: UUID | None = None, word: str = "pointers") -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": word}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "<script>alert(1)</script>"}]},
        {
            "type": "codeBlock",
            "attrs": {"language": "python"},
            "content": [{"type": "text", "text": "def two_sum(a):\n    return a"}],
        },
    ]
    if image is not None:
        content.append({"type": "image", "attrs": {"file_id": str(image), "alt": "diagram"}})
    return {"type": "doc", "content": content}


async def set_doc(
    api: CourseApi, campus: Campus, course: BuiltCourse, lesson: UUID, doc: dict[str, Any]
) -> Any:
    return await api.request(
        "PATCH", f"/courses/{course.id}/lessons/{lesson}", campus.author, campus.p,
        json={"content": {"doc": doc}},
    )  # fmt: skip


async def test_invalid_notes_are_rejected_with_their_path(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes",)])
    bad = {"type": "doc", "content": [{"type": "paragraph"}, {"type": "iframe"}]}
    response = await set_doc(api, campus, course, course.lesson_ids[0], bad)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_lesson_content"
    assert "doc[1]: node type 'iframe' is not allowed" in error["details"][0]["msg"]


async def test_notes_images_must_be_this_orgs_confirmed_images(
    api: CourseApi, campus: Campus
) -> None:
    course = await api.build(campus.author, campus.p, [("notes",)])
    lesson = course.lesson_ids[0]
    for image in [
        await api.factory.image(campus.c),  # another org's
        await api.factory.image(campus.p, status="pending"),  # not confirmed
        await api.factory.pdf(campus.p),  # not an image
    ]:
        response = await set_doc(api, campus, course, lesson, notes_doc(image=image.id))
        assert response.status_code == 422, image
        assert response.json()["error"]["code"] == "invalid_image"
        assert response.json()["error"]["details"]["file_ids"] == [str(image.id)]
    own = await api.factory.image(campus.p)
    ok(await set_doc(api, campus, course, lesson, notes_doc(image=own.id)))


async def test_publish_renders_notes_and_readers_never_get_editor_json(
    api: CourseApi, campus: Campus
) -> None:
    image = await api.factory.image(campus.p)
    course = await api.build(campus.author, campus.p, [("notes",)])
    lesson = course.lesson_ids[0]
    ok(await set_doc(api, campus, course, lesson, notes_doc(image=image.id)))
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    ok(await api.assign(campus.author, campus.p, course.id, to=campus.c), 201)
    ok(await api.assign(campus.c_admin, campus.c, course.id, batches=[campus.cse_batch]), 201)
    await api.run_jobs()
    enrollment = await api.enrollment_for(campus.cse, campus.c, course.id)
    assert enrollment

    async def student_content() -> dict[str, Any]:
        detail = ok(await api.request("GET", f"/enrollments/{enrollment['id']}", campus.cse,
                                      campus.c))  # fmt: skip
        content: dict[str, Any] = detail["outline"]["modules"][0]["lessons"][0]["content"]
        return content

    content = await student_content()
    assert set(content) == {"html", "image_file_ids"}  # no Tiptap JSON for readers
    assert content["image_file_ids"] == [str(image.id)]
    html = content["html"]
    assert "<h2>pointers</h2>" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in html
    assert '<span class="k">def</span>' in html
    assert f'<img data-file-id="{image.id}" alt="diagram">' in html

    # Later draft edits don't reach the published version.
    ok(await set_doc(api, campus, course, lesson, notes_doc(word="edited")))
    assert (await student_content())["html"] == html

    images = ok(
        await api.request(
            "GET", f"/enrollments/{enrollment['id']}/lessons/{lesson}/images", campus.cse,
            campus.c,
        )
    )  # fmt: skip
    url = images["urls"][str(image.id)]
    assert parse_qs(urlsplit(url).query)["X-Amz-Expires"] == ["300"]
    other = await api.request(
        "GET", f"/enrollments/{enrollment['id']}/lessons/{lesson}/images", campus.ece, campus.c
    )
    assert other.status_code == 404


async def test_draft_preview_renders_like_publishing(api: CourseApi, campus: Campus) -> None:
    image = await api.factory.image(campus.p)
    course = await api.build(campus.author, campus.p, [("notes", "pdf")])
    notes_lesson, pdf_lesson = course.lesson_ids
    ok(await set_doc(api, campus, course, notes_lesson, notes_doc(image=image.id)))

    preview = ok(
        await api.request(
            "GET", f"/courses/{course.id}/lessons/{notes_lesson}/preview", campus.author, campus.p
        )
    )
    assert "<h2>pointers</h2>" in preview["html"]
    assert str(image.id) in preview["image_urls"]
    assert preview["expires_at"]
    not_notes = await api.request(
        "GET", f"/courses/{course.id}/lessons/{pdf_lesson}/preview", campus.author, campus.p
    )
    assert not_notes.status_code == 404


async def test_notes_complete_by_marking_them_done(api: CourseApi, campus: Campus) -> None:
    course = await published_for_cse(api, campus, modules=(("notes",),))
    enrollment = await api.enrollment_for(campus.cse, campus.c, course.id)
    assert enrollment
    done = ok(
        await api.request(
            "POST", f"/enrollments/{enrollment['id']}/lessons/{course.lesson_ids[0]}/complete",
            campus.cse, campus.c,
        )
    )  # fmt: skip
    assert done["lesson"]["status"] == "completed"
    assert done["enrollment"]["progress_percent"] == 100
