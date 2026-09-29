"""Local storage is real MinIO. Bunny HTTP is mocked; no credentials leave these tests."""

import asyncio
import hashlib
import struct
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import update

from app.core.config import Settings
from app.core.storage import ObjectStorage
from app.db.base import new_id
from app.modules.media import service
from app.modules.media.models import VideoAsset
from app.modules.media.mp4 import NotAnMp4Error, mp4_duration_seconds
from app.modules.media.providers import BunnyStreamProvider, LocalVideoProvider, bunny_signed_url
from tests.course_api import Campus, CourseApi, ok

FIXTURE = Path(__file__).resolve().parents[4] / "tests/fixtures/video.mp4"
GUID = "00000000-0000-4000-8000-000000000123"


def bunny_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "bunny_library_id": "42",
            "bunny_api_key": SecretStr("test-api-key"),
            "bunny_token_key": SecretStr("test-token-key"),
            "bunny_webhook_secret": SecretStr("test-hook"),
            "bunny_cdn_hostname": "test.b-cdn.net",
        }
    )


async def test_local_signed_upload_process_playback(api: CourseApi, campus: Campus) -> None:
    created = ok(
        await api.request("POST", "/videos", campus.author, campus.p, json={"title": "Demo"}), 201
    )
    upload, video = created["upload"], created["video"]
    assert upload["protocol"] == "s3_put"
    content = FIXTURE.read_bytes()
    async with httpx.AsyncClient() as http:
        response = await http.put(upload["url"], headers=upload["headers"], content=content)
        assert response.status_code == 200, response.text
    ok(await api.request("POST", f"/videos/{video['id']}/uploaded", campus.author, campus.p))
    assert (
        await service.process_video(
            api.app.state.sessionmaker, api.app.state.video_providers, UUID(video["id"])
        )
        == "ready"
    )
    playback = ok(
        await api.request("GET", f"/videos/{video['id']}/playback", campus.author, campus.p)
    )
    assert playback["kind"] == "mp4"
    async with httpx.AsyncClient() as http:
        response = await http.get(playback["url"])
        assert response.content == content
        assert (
            await http.get(playback["url"].replace("X-Amz-Signature=", "X-Amz-Signature=bad"))
        ).status_code == 403
        assert (await http.get(playback["url"].split("?")[0])).status_code == 403
    provider = api.app.state.video_providers["local"]
    await provider.delete(f"videos/{video['id']}/source.mp4")


async def test_local_upload_rejects_wrong_type_and_oversize(api: CourseApi, campus: Campus) -> None:
    created = ok(
        await api.request("POST", "/videos", campus.author, campus.p, json={"title": "Big"}), 201
    )
    upload, video = created["upload"], created["video"]
    content = FIXTURE.read_bytes()
    async with httpx.AsyncClient() as http:
        # Content-Type is part of the signature: anything else is refused by storage.
        wrong = await http.put(
            upload["url"], headers={"Content-Type": "text/html"}, content=b"<script>"
        )
        assert wrong.status_code == 403
        assert (
            await http.put(upload["url"], headers=upload["headers"], content=content)
        ).status_code == 200
    ok(await api.request("POST", f"/videos/{video['id']}/uploaded", campus.author, campus.p))
    storage = ObjectStorage(api.app.state.settings)
    small = api.app.state.settings.model_copy(update={"video_upload_max_bytes": len(content) - 1})
    providers = {"local": LocalVideoProvider(small, storage)}
    key = f"videos/{video['id']}/source.mp4"

    status = await service.process_video(api.app.state.sessionmaker, providers, UUID(video["id"]))

    assert status == "failed"
    assert storage.size(key) is None  # the rejected object is deleted
    detail = ok(await api.request("GET", f"/videos/{video['id']}", campus.author, campus.p))
    assert detail["status"] == "failed"


async def test_local_expiry_and_invalid_file(settings: Settings) -> None:
    storage = ObjectStorage(settings)
    provider = LocalVideoProvider(settings, storage)

    created = await provider.create_upload(new_id(), "Invalid")
    try:
        await storage.aput_bytes(created.provider_video_id, b"not a video", "video/mp4")
        assert (await provider.refresh_status(created.provider_video_id)).status == "failed"
        playback = await provider.playback(created.provider_video_id, 1)
        await asyncio.sleep(2)
        async with httpx.AsyncClient() as http:
            assert (await http.get(playback.url)).status_code == 403
    finally:
        await provider.delete(created.provider_video_id)


@respx.mock
async def test_bunny_upload_and_playback(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.modules.media.providers.time.time", lambda: 1700000000)
    create = respx.post("https://video.bunnycdn.com/library/42/videos").mock(
        return_value=httpx.Response(200, json={"guid": GUID})
    )
    async with httpx.AsyncClient() as http:
        provider = BunnyStreamProvider(bunny_settings(settings), http)
        result = await provider.create_upload(UUID(GUID), "Demo")
        assert create.calls[0].request.headers["AccessKey"] == "test-api-key"
        headers = result.ticket.headers
        expires = 1700000000 + settings.video_upload_ttl_seconds
        assert headers == {
            "LibraryId": "42",
            "VideoId": GUID,
            "AuthorizationExpire": str(expires),
            "AuthorizationSignature": hashlib.sha256(
                f"42test-api-key{expires}{GUID}".encode()
            ).hexdigest(),
        }
        assert result.ticket.protocol == "tus"
        assert result.ticket.url == "https://video.bunnycdn.com/tusupload"
        playback = await provider.playback(GUID, 300)
        assert playback.kind == "hls"
        assert int(playback.expires_at.timestamp()) == 1700000300
        assert playback.url.endswith(f"/{GUID}/playlist.m3u8")
        token = parse_qs(urlsplit(playback.url).path.split("/")[1])
        assert token["expires"] == ["1700000300"]
        assert token["token_path"] == [f"/{GUID}/"]
        assert playback.url != bunny_signed_url(
            hostname="test.b-cdn.net",
            token_key="test-token-key",
            directory=f"/{GUID}/",
            path=f"/{GUID}/playlist.m3u8",
            expires=1700000000,
        )


@respx.mock
async def test_bunny_webhook_refetches_status(
    api: CourseApi, campus: Campus, monkeypatch: pytest.MonkeyPatch
) -> None:

    video = await api.factory.video(campus.p)
    async with api.factory.sessionmaker() as session, session.begin():
        await session.execute(
            update(VideoAsset)
            .where(VideoAsset.id == video.id)
            .values(provider="bunny", provider_video_id=GUID)
        )
    settings = bunny_settings(api.app.state.settings)
    async with httpx.AsyncClient() as http:
        provider = BunnyStreamProvider(settings, http)
        monkeypatch.setattr(api.app.state, "settings", settings)
        monkeypatch.setattr(api.app.state, "video_providers", {"bunny": provider})
        status = respx.get(f"https://video.bunnycdn.com/library/42/videos/{GUID}").mock(
            return_value=httpx.Response(200, json={"status": 2})
        )
        payload = {"VideoGuid": GUID, "Status": 4}
        assert (
            await api.client.post("/api/v1/webhooks/video/bunny/wrong", json=payload)
        ).status_code == 404
        assert not status.called
        assert (
            await api.client.post("/api/v1/webhooks/video/bunny/test-hook", json=payload)
        ).status_code == 200
        assert (
            ok(await api.request("GET", f"/videos/{video.id}", campus.author, campus.p))["status"]
            == "processing"
        )
        status.mock(return_value=httpx.Response(200, json={"status": 4, "length": 12}))
        assert (
            await api.client.post("/api/v1/webhooks/video/bunny/test-hook", json=payload)
        ).status_code == 200
        ready = ok(await api.request("GET", f"/videos/{video.id}", campus.author, campus.p))
        assert ready["status"] == "ready"
        assert ready["duration_seconds"] == 12


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"invalid",
        struct.pack(">I4s", 8, b"ftyp")
        + struct.pack(">I4s", 16, b"moov")
        + struct.pack(">I4s", 8, b"mvhd"),
    ],
)
def test_invalid_mp4_is_rejected(body: bytes) -> None:
    with pytest.raises(NotAnMp4Error):
        mp4_duration_seconds(lambda start, length: body[start : start + length], len(body))
