"""PDF and image uploads against real MinIO: presigned POST limits, confirm checks, downloads."""

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.storage import ObjectStorage
from tests.course_api import Campus, CourseApi, ok

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 24
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 16


async def create(
    api: CourseApi, campus: Campus, kind: str = "pdf", content_type: str = "application/pdf",
    name: str = "notes.pdf",
) -> dict[str, Any]:  # fmt: skip
    body = {"kind": kind, "file_name": name, "content_type": content_type}
    created: dict[str, Any] = ok(
        await api.request("POST", "/files", campus.author, campus.p, json=body), 201
    )
    return created


async def post_upload(
    upload: dict[str, Any], content: bytes, **field_overrides: str
) -> httpx.Response:
    fields = {**upload["fields"], **field_overrides}
    async with httpx.AsyncClient() as http:
        return await http.post(upload["url"], data=fields, files={"file": ("f", content)})


async def confirm(api: CourseApi, campus: Campus, file_id: str) -> httpx.Response:
    return await api.request("POST", f"/files/{file_id}/confirm", campus.author, campus.p)


async def test_pdf_upload_confirm_and_signed_download(api: CourseApi, campus: Campus) -> None:
    created = await create(api, campus)
    file, upload = created["file"], created["upload"]
    assert file["status"] == "pending"
    assert upload["max_bytes"] == 25 * 1024**2

    assert (await post_upload(upload, PDF)).status_code == 204
    confirmed = ok(await confirm(api, campus, file["id"]))
    assert confirmed["status"] == "ready"
    assert confirmed["size_bytes"] == len(PDF)
    assert ok(await confirm(api, campus, file["id"]))["status"] == "ready"  # idempotent

    download = ok(
        await api.request("GET", f"/files/{file['id']}/download", campus.author, campus.p)
    )
    assert parse_qs(urlsplit(download["url"]).query)["X-Amz-Expires"] == ["300"]
    async with httpx.AsyncClient() as http:
        response = await http.get(download["url"])
        assert response.content == PDF
        assert 'filename="notes.pdf"' in response.headers["content-disposition"]
        tampered = download["url"].replace("X-Amz-Signature=", "X-Amz-Signature=0")
        assert (await http.get(tampered)).status_code == 403
        assert (await http.get(download["url"].split("?")[0])).status_code == 403  # private


async def test_presigned_post_enforces_content_type_and_size(
    api: CourseApi, campus: Campus, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload = (await create(api, campus))["upload"]
    wrong_type = await post_upload(upload, PDF, **{"Content-Type": "text/html"})
    assert wrong_type.status_code == 403  # the policy pins Content-Type

    small = api.app.state.settings.model_copy(update={"pdf_upload_max_bytes": 64})
    monkeypatch.setattr(api.app.state, "settings", small)
    upload = (await create(api, campus))["upload"]
    assert upload["max_bytes"] == 64
    too_big = await post_upload(upload, PDF + b"x" * 64)
    assert too_big.status_code == 400
    assert "EntityTooLarge" in too_big.text


async def test_confirm_rejects_non_pdf_bytes_and_deletes_them(
    api: CourseApi, campus: Campus
) -> None:
    created = await create(api, campus)
    file, upload = created["file"], created["upload"]
    assert (await post_upload(upload, b"<html><script>alert(1)</script></html>")).status_code == 204

    rejected = ok(await confirm(api, campus, file["id"]))

    assert rejected["status"] == "rejected"
    assert "don't match" in rejected["error"]
    storage = ObjectStorage(api.app.state.settings)
    assert storage.size(f"files/{campus.p.id}/{file['id']}/pdf") is None
    again = await confirm(api, campus, file["id"])
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "file_rejected"
    # A rejected file can never be attached to a lesson.
    course = await api.build(campus.author, campus.p, [()])
    lesson = await api.request(
        "POST", f"/courses/{course.id}/modules/{course.module_ids[0]}/lessons", campus.author,
        campus.p, json={"title": "P", "lesson_type": "pdf", "content": {"file_id": file["id"]}},
    )  # fmt: skip
    assert lesson.status_code == 422
    assert lesson.json()["error"]["code"] == "file_not_ready"


async def test_confirm_before_upload_keeps_the_file_pending(api: CourseApi, campus: Campus) -> None:
    file = (await create(api, campus))["file"]
    response = await confirm(api, campus, file["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "upload_not_found"
    detail = ok(await api.request("GET", f"/files/{file['id']}", campus.author, campus.p))
    assert detail["status"] == "pending"


@pytest.mark.parametrize(
    ("content_type", "content", "status"),
    [
        ("image/png", PNG, "ready"),
        ("image/jpeg", JPEG, "ready"),
        ("image/webp", WEBP, "ready"),
        ("image/png", JPEG, "rejected"),  # declared PNG, bytes are JPEG
        ("image/png", PDF, "rejected"),
    ],
)
async def test_image_contents_must_match_the_declared_type(
    api: CourseApi, campus: Campus, content_type: str, content: bytes, status: str
) -> None:
    created = await create(api, campus, "image", content_type, "diagram")
    assert created["upload"]["max_bytes"] == 5 * 1024**2
    assert (await post_upload(created["upload"], content)).status_code == 204
    assert ok(await confirm(api, campus, created["file"]["id"]))["status"] == status


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "pdf", "file_name": "a.pdf", "content_type": "image/png"},
        {"kind": "image", "file_name": "a.png", "content_type": "application/pdf"},
        {"kind": "image", "file_name": "a.svg", "content_type": "image/svg+xml"},
        {"kind": "pdf", "file_name": 'a".pdf', "content_type": "application/pdf"},
        {"kind": "pdf", "file_name": "a\r\nX-Injected: 1", "content_type": "application/pdf"},
        {"kind": "pdf", "file_name": "../../etc/passwd", "content_type": "application/pdf"},
        {"kind": "video", "file_name": "a.mp4", "content_type": "application/pdf"},
    ],
)
async def test_invalid_file_requests_are_rejected(
    api: CourseApi, campus: Campus, body: dict[str, str]
) -> None:
    response = await api.request("POST", "/files", campus.author, campus.p, json=body)
    assert response.status_code == 422, response.text


async def test_other_orgs_cannot_see_or_manage_files(api: CourseApi, campus: Campus) -> None:
    file = await api.factory.pdf(campus.p)
    for user, org in [(campus.c_admin, campus.c), (campus.c_instructor, campus.c)]:
        for method, suffix in [("GET", ""), ("POST", "/confirm"), ("GET", "/download")]:
            response = await api.request(method, f"/files/{file.id}{suffix}", user, org)
            assert response.status_code == 404, (method, suffix)
    listed = ok(await api.request("GET", "/files", campus.c_instructor, campus.c))
    assert str(file.id) not in {f["id"] for f in listed["items"]}
