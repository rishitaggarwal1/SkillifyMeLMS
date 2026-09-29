"""Video providers behind one interface.

- `LocalVideoProvider` (dev and CI): the browser uploads the MP4 to MinIO with a presigned PUT;
  "processing" is a Celery task that reads the duration from the file; playback is a short-lived
  signed MinIO URL for the MP4.
- `BunnyStreamProvider`: the video is created through the Bunny Stream API and the browser uploads
  it directly with TUS using Bunny's presigned headers; Bunny calls our webhook when processing
  finishes (we re-fetch the status rather than trusting the body); playback is an HLS manifest URL
  signed with the CDN token-authentication key.

Providers never see our database; the media service stores what they return.
"""

import asyncio
import base64
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from urllib.parse import quote
from uuid import UUID

import httpx

from app.core.config import Settings
from app.core.storage import ObjectStorage
from app.modules.media.models import VideoStatus
from app.modules.media.mp4 import NotAnMp4Error, mp4_duration_seconds

UploadProtocol = Literal["s3_put", "tus"]
PlaybackKind = Literal["mp4", "hls"]


@dataclass(frozen=True, slots=True)
class UploadTicket:
    protocol: UploadProtocol
    url: str
    expires_at: datetime
    fields: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)  # tus: headers for the upload


@dataclass(frozen=True, slots=True)
class CreatedUpload:
    provider_video_id: str
    ticket: UploadTicket


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    status: VideoStatus
    duration_seconds: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Playback:
    url: str
    kind: PlaybackKind
    expires_at: datetime


class VideoProvider(Protocol):
    name: str

    async def create_upload(self, asset_id: UUID, title: str) -> CreatedUpload: ...

    async def refresh_status(self, provider_video_id: str) -> ProviderStatus: ...

    async def playback(self, provider_video_id: str, ttl_seconds: int) -> Playback: ...

    async def delete(self, provider_video_id: str) -> None: ...

    def handle_webhook(self, body: object) -> str | None: ...


def _expires_at(ttl_seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=ttl_seconds)


# ============================================================================ local (MinIO)


class LocalVideoProvider:
    name = "local"
    content_type = "video/mp4"

    def __init__(self, settings: Settings, storage: ObjectStorage) -> None:
        self.settings = settings
        self.storage = storage

    async def create_upload(self, asset_id: UUID, title: str) -> CreatedUpload:
        del title
        key = f"videos/{asset_id}/source.mp4"
        ttl = self.settings.video_upload_ttl_seconds
        url = await asyncio.to_thread(
            self.storage.presigned_put,
            key,
            content_type=self.content_type,
            expires_in=ttl,
        )
        ticket = UploadTicket(
            protocol="s3_put",
            url=url,
            headers={"Content-Type": self.content_type},
            expires_at=_expires_at(ttl),
        )
        return CreatedUpload(provider_video_id=key, ticket=ticket)

    def _inspect(self, key: str) -> ProviderStatus:
        size = self.storage.size(key)
        if size is None:
            return ProviderStatus(VideoStatus.CREATED)  # not uploaded yet
        if not 0 < size <= self.settings.video_upload_max_bytes:
            return ProviderStatus(VideoStatus.FAILED, error="Video exceeds the upload size limit.")
        try:
            duration = mp4_duration_seconds(
                lambda offset, length: self.storage.read_range(key, offset, length), size
            )
        except NotAnMp4Error as exc:
            return ProviderStatus(VideoStatus.FAILED, error=f"Not a playable MP4: {exc}")
        return ProviderStatus(VideoStatus.READY, duration_seconds=max(1, round(duration)))

    async def refresh_status(self, provider_video_id: str) -> ProviderStatus:
        return await asyncio.to_thread(self._inspect, provider_video_id)

    async def playback(self, provider_video_id: str, ttl_seconds: int) -> Playback:
        url = await asyncio.to_thread(
            self.storage.presigned_get, provider_video_id, expires_in=ttl_seconds
        )
        return Playback(url=url, kind="mp4", expires_at=_expires_at(ttl_seconds))

    async def delete(self, provider_video_id: str) -> None:
        await asyncio.to_thread(self.storage.delete, provider_video_id)

    def handle_webhook(self, body: object) -> str | None:
        return None  # local processing is driven by Celery after upload confirmation


# ============================================================================ Bunny Stream

# https://docs.bunny.net/reference/video_getvideo - "status"
_BUNNY_STATUS = {
    0: VideoStatus.CREATED,  # created, waiting for upload
    1: VideoStatus.PROCESSING,  # uploaded
    2: VideoStatus.PROCESSING,  # processing
    3: VideoStatus.PROCESSING,  # transcoding
    4: VideoStatus.READY,  # finished
    5: VideoStatus.FAILED,  # error
    6: VideoStatus.FAILED,  # upload failed
}


class BunnyError(RuntimeError):
    pass


def bunny_upload_signature(library_id: str, api_key: str, expires: int, video_id: str) -> str:
    """Bunny's presigned TUS upload signature: sha256(library_id + api_key + expiration +
    video_id), hex."""
    return hashlib.sha256(f"{library_id}{api_key}{expires}{video_id}".encode()).hexdigest()


def bunny_signed_url(
    *, hostname: str, token_key: str, directory: str, path: str, expires: int
) -> str:
    """A CDN token-authenticated URL (SHA256 token authentication) whose token covers a whole
    directory, embedded in the path so the player's relative HLS segment requests carry it too.

    token = urlsafe_base64(sha256(key + token_path + expires + "token_path=" + token_path))
    """
    parameters = f"token_path={directory}"
    digest = hashlib.sha256(f"{token_key}{directory}{expires}{parameters}".encode()).digest()
    token = base64.b64encode(digest).decode().replace("+", "-").replace("/", "_").rstrip("=")
    encoded_dir = quote(directory, safe="")
    return f"https://{hostname}/bcdn_token={token}&expires={expires}&token_path={encoded_dir}{path}"


class BunnyStreamProvider:
    name = "bunny"

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        if not settings.bunny_configured:
            msg = "Bunny Stream is not configured (BUNNY_* settings)"
            raise BunnyError(msg)
        assert settings.bunny_api_key is not None  # noqa: S101 - checked above
        assert settings.bunny_token_key is not None  # noqa: S101
        self.settings = settings
        self.http = http
        self.library_id = str(settings.bunny_library_id)
        self.api_key = settings.bunny_api_key.get_secret_value()
        self.token_key = settings.bunny_token_key.get_secret_value()
        self.cdn_hostname = str(settings.bunny_cdn_hostname)
        self.api = f"{settings.bunny_api_url.rstrip('/')}/library/{self.library_id}/videos"

    async def _call(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.http.request(
            method,
            url,
            headers={"AccessKey": self.api_key, "Accept": "application/json"},
            timeout=15.0,
            **kwargs,
        )
        if response.status_code >= 400:  # noqa: PLR2004
            msg = f"Bunny API {method} failed with {response.status_code}"
            raise BunnyError(msg)
        return dict(response.json()) if response.content else {}

    async def create_upload(self, asset_id: UUID, title: str) -> CreatedUpload:
        created = await self._call("POST", self.api, json={"title": title[:200]})
        video_id = str(created["guid"])
        ttl = self.settings.video_upload_ttl_seconds
        expires = int(time.time()) + ttl
        ticket = UploadTicket(
            protocol="tus",
            url=f"{self.settings.bunny_api_url.rstrip('/')}/tusupload",
            headers={
                "AuthorizationSignature": bunny_upload_signature(
                    self.library_id, self.api_key, expires, video_id
                ),
                "AuthorizationExpire": str(expires),
                "VideoId": video_id,
                "LibraryId": self.library_id,
            },
            expires_at=datetime.fromtimestamp(expires, UTC),
        )
        del asset_id
        return CreatedUpload(provider_video_id=video_id, ticket=ticket)

    async def refresh_status(self, provider_video_id: str) -> ProviderStatus:
        video = await self._call("GET", f"{self.api}/{provider_video_id}")
        status = _BUNNY_STATUS.get(int(video.get("status", 2)), VideoStatus.PROCESSING)
        length = video.get("length")
        return ProviderStatus(
            status=status,
            duration_seconds=int(length) if status == VideoStatus.READY and length else None,
            error="Bunny could not process the video" if status == VideoStatus.FAILED else None,
        )

    async def playback(self, provider_video_id: str, ttl_seconds: int) -> Playback:
        expires = int(time.time()) + ttl_seconds
        url = bunny_signed_url(
            hostname=self.cdn_hostname,
            token_key=self.token_key,
            directory=f"/{provider_video_id}/",
            path=f"/{provider_video_id}/playlist.m3u8",
            expires=expires,
        )
        return Playback(url=url, kind="hls", expires_at=datetime.fromtimestamp(expires, UTC))

    async def delete(self, provider_video_id: str) -> None:
        await self._call("DELETE", f"{self.api}/{provider_video_id}")

    def handle_webhook(self, body: object) -> str | None:
        return bunny_webhook_video_id(body)


def bunny_webhook_video_id(body: Any) -> str | None:
    """The video guid named by a Bunny webhook body (the only field we use; the status is
    re-fetched from the API)."""
    if isinstance(body, dict):
        guid = body.get("VideoGuid")
        if isinstance(guid, str):
            try:
                return str(UUID(guid))
            except ValueError:
                return None
    return None


# ============================================================================ registry


def create_providers(
    settings: Settings, storage: ObjectStorage, http: httpx.AsyncClient
) -> dict[str, VideoProvider]:
    """Every provider that can serve existing assets (assets remember their provider), keyed by
    name. New uploads use `settings.video_provider`."""
    providers: dict[str, VideoProvider] = {"local": LocalVideoProvider(settings, storage)}
    if settings.bunny_configured:
        providers["bunny"] = BunnyStreamProvider(settings, http)
    elif settings.video_provider == "bunny":
        msg = "VIDEO_PROVIDER=bunny needs every BUNNY_* setting"
        raise BunnyError(msg)
    return providers
