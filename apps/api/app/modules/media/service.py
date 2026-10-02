"""Media module public interface.

Videos: an editor creates an asset and gets an upload ticket for the configured provider; the
browser uploads directly to the provider (never through our API); the asset becomes `ready` when
processing finishes (a Celery task for the local provider, the Bunny webhook in production).
Playback URLs are short-lived and signed; students get them only for videos in courses they can
read (RLS on `video_assets`, migration 0006).

Files (PDFs for pdf lessons, images for notes): an editor creates a pending file and gets a
presigned POST limited to one key, one Content-Type and a size range; `confirm` then checks the
stored object's size and leading bytes. Downloads are signed GET URLs that expire in minutes;
students get them only for files their course version uses (RLS on `files`, migration 0007).
"""

import asyncio
import hmac
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.logging import get_logger
from app.core.pagination import CursorParams
from app.core.storage import ObjectStorage
from app.db.base import new_id
from app.db.session import run_after_commit
from app.db.tenancy import independent_transaction, system_transaction
from app.modules.audit import service as audit
from app.modules.identity.authz import Permission, require_org, require_org_permission
from app.modules.identity.dependencies import RequestContext
from app.modules.media.models import FileKind, FileStatus, StoredFile, VideoAsset, VideoStatus
from app.modules.media.providers import (
    LocalVideoProvider,
    Playback,
    ProviderStatus,
    VideoProvider,
    _expires_at,
)
from app.modules.media.repository import FileRepository, VideoRepository
from app.modules.media.schemas import (
    FileCreate,
    FileOut,
    FileUploadOut,
    PresignedPostOut,
    SubmissionContentType,
    UploadTicketOut,
    VideoOut,
    VideoUploadOut,
)
from app.modules.media.schemas import (
    FileDownloadOut as FileDownloadOut,  # noqa: PLC0414 - public interface
)
from app.modules.media.schemas import PlaybackOut as PlaybackOut  # noqa: PLC0414 - public interface

logger = get_logger(__name__)
Providers = Mapping[str, VideoProvider]
PROCESS_VIDEO = "media.process_video"


@dataclass(frozen=True, slots=True)
class VideoInfo:
    id: UUID
    organization_id: UUID
    status: str
    duration_seconds: int | None

    @property
    def is_ready(self) -> bool:
        return self.status == VideoStatus.READY


@dataclass(frozen=True, slots=True)
class FileInfo:
    id: UUID
    organization_id: UUID
    kind: str
    status: str
    file_name: str = ""
    content_type: str = ""

    @property
    def is_ready(self) -> bool:
        return self.status == FileStatus.READY


# ============================================================================ interface


async def videos(session: AsyncSession, ids: Sequence[UUID]) -> dict[UUID, VideoInfo]:
    """Video assets visible to the caller."""
    if not ids:
        return {}
    rows = await session.execute(
        select(
            VideoAsset.id, VideoAsset.organization_id, VideoAsset.status,
            VideoAsset.duration_seconds,
        ).where(VideoAsset.id.in_(ids))
    )  # fmt: skip
    return {r.id: VideoInfo(r.id, r.organization_id, r.status, r.duration_seconds) for r in rows}


async def files(session: AsyncSession, ids: Sequence[UUID]) -> dict[UUID, FileInfo]:
    if not ids:
        return {}
    rows = await session.execute(
        select(
            StoredFile.id, StoredFile.organization_id, StoredFile.kind, StoredFile.status,
            StoredFile.file_name, StoredFile.content_type,
        ).where(StoredFile.id.in_(ids))
    )  # fmt: skip
    return {
        r.id: FileInfo(r.id, r.organization_id, r.kind, r.status, r.file_name, r.content_type)
        for r in rows
    }


async def playback_for(
    session: AsyncSession, providers: Providers, video_id: UUID, ttl_seconds: int
) -> Playback | None:
    """A signed playback URL for a ready video the caller may read, else None."""
    asset = await VideoRepository(session).get(video_id)
    if asset is None or asset.status != VideoStatus.READY:
        return None
    return await providers[asset.provider].playback(asset.provider_video_id, ttl_seconds)


# ============================================================================ editor API


def _out(asset: VideoAsset) -> VideoOut:
    return VideoOut.model_validate(asset)


def _playback_out(playback: Playback) -> PlaybackOut:
    return PlaybackOut(url=playback.url, kind=playback.kind, expires_at=playback.expires_at)


async def _own_video(ctx: RequestContext, video_id: UUID) -> VideoAsset:
    org_id = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    asset = await VideoRepository(ctx.session).get(video_id)
    # Readers of a course may see its videos through RLS; managing them is the owner's.
    if asset is None or (asset.organization_id != org_id and not ctx.principal.is_platform_admin):
        raise NotFoundError("Video not found.")
    return asset


async def create_video(
    ctx: RequestContext, providers: Providers, settings: Settings, title: str
) -> VideoUploadOut:
    org_id = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    provider = providers[settings.video_provider]
    asset_id = new_id()
    created = await provider.create_upload(asset_id, title)
    asset = await VideoRepository(ctx.session).create(
        VideoAsset(
            id=asset_id,
            organization_id=org_id,
            provider=provider.name,
            provider_video_id=created.provider_video_id,
            title=title,
            status=VideoStatus.CREATED,
            created_by=ctx.principal.user_id,
        )
    )
    ticket = created.ticket
    await audit.record(
        ctx.session,
        ctx.actor,
        action="video.created",
        target_type="video",
        target_id=asset.id,
        after={"title": title, "provider": provider.name},
    )
    return VideoUploadOut(
        video=_out(asset),
        upload=UploadTicketOut(
            protocol=ticket.protocol, url=ticket.url, fields=ticket.fields,
            headers=ticket.headers, expires_at=ticket.expires_at,
        ),
    )  # fmt: skip


async def list_videos(
    ctx: RequestContext, params: CursorParams, status: str | None
) -> tuple[list[VideoOut], str | None]:
    org_id = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    rows, cursor = await VideoRepository(ctx.session).list_page(org_id, params, status=status)
    return [_out(a) for a in rows], cursor


async def get_video(ctx: RequestContext, video_id: UUID) -> VideoOut:
    return _out(await _own_video(ctx, video_id))


async def mark_uploaded(ctx: RequestContext, video_id: UUID) -> VideoOut:
    """The browser finished uploading: start processing (idempotent)."""
    asset = await _own_video(ctx, video_id)
    if asset.status == VideoStatus.READY:
        return _out(asset)
    repo = VideoRepository(ctx.session)
    await repo.update(video_id, {"status": VideoStatus.PROCESSING, "error": None})
    await audit.record(
        ctx.session,
        ctx.actor,
        action="video.uploaded",
        target_type="video",
        target_id=video_id,
    )
    jobs = ctx.jobs
    run_after_commit(ctx.session, lambda: jobs.send(PROCESS_VIDEO, str(video_id)))
    refreshed = await repo.get(video_id)
    assert refreshed is not None  # noqa: S101
    return _out(refreshed)


async def editor_playback(
    ctx: RequestContext, providers: Providers, settings: Settings, video_id: UUID
) -> PlaybackOut:
    asset = await _own_video(ctx, video_id)
    if asset.status != VideoStatus.READY:
        raise ConflictError("The video isn't ready yet.", code="video_not_ready")
    playback = await providers[asset.provider].playback(
        asset.provider_video_id, settings.video_playback_ttl_seconds
    )
    return _playback_out(playback)


# ============================================================================ operator imports
# For seeds and scripts (no HTTP route): store bytes the browser would otherwise upload, then run
# the same checks a confirm or processing step runs. Requires `course.edit` like editor uploads.


async def import_local_video(
    ctx: RequestContext, storage: ObjectStorage, settings: Settings, *, title: str, data: bytes
) -> VideoOut:
    """An MP4 stored for the local provider and processed at once (size and MP4 checks)."""
    org_id = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    provider = LocalVideoProvider(settings, storage)
    asset_id = new_id()
    key = f"videos/{asset_id}/source.mp4"
    await storage.aput_bytes(key, data, provider.content_type)
    asset = await VideoRepository(ctx.session).create(
        VideoAsset(
            id=asset_id, organization_id=org_id, provider=provider.name, provider_video_id=key,
            title=title, status=VideoStatus.CREATED, created_by=ctx.principal.user_id,
        )
    )  # fmt: skip
    status = await refresh_video(ctx.session, {provider.name: provider}, asset)
    if status != VideoStatus.READY:
        msg = f"Imported video {title!r} isn't playable ({status})"
        raise UnprocessableError(msg, code="video_not_ready")
    refreshed = await VideoRepository(ctx.session).get(asset_id)
    assert refreshed is not None  # noqa: S101
    return _out(refreshed)


async def import_file(
    ctx: RequestContext,
    storage: ObjectStorage,
    settings: Settings,
    *,
    kind: str,
    file_name: str,
    content_type: str,
    data: bytes,
) -> FileOut:
    """A PDF or image stored and confirmed (size and leading-bytes checks)."""
    org_id = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    file_id = new_id()
    key = f"files/{org_id}/{file_id}/{kind}"
    await storage.aput_bytes(key, data, content_type)
    file = await FileRepository(ctx.session).create(
        StoredFile(
            id=file_id, organization_id=org_id, kind=kind, storage_key=key, file_name=file_name,
            content_type=content_type, status=FileStatus.PENDING,
            created_by=ctx.principal.user_id,
        )
    )  # fmt: skip
    return await _confirm(ctx, storage, settings, file)


# ============================================================================ processing


def _transition(asset: VideoAsset, found: ProviderStatus) -> dict[str, object] | None:
    """New column values after asking the provider, or None if nothing changes."""
    if asset.status == VideoStatus.READY:
        return None  # never downgrade a ready video
    status = found.status
    if status == VideoStatus.CREATED:
        if asset.provider != "local" or asset.status != VideoStatus.PROCESSING:
            return None
        # Local uploads are synchronous: "uploaded" but no object means the upload failed.
        return {"status": VideoStatus.FAILED, "error": "The upload was not found."}
    return {"status": status, "duration_seconds": found.duration_seconds, "error": found.error}


async def refresh_video(session: AsyncSession, providers: Providers, asset: VideoAsset) -> str:
    """Ask the provider for the asset's status and store it; returns the new status."""
    found = await providers[asset.provider].refresh_status(asset.provider_video_id)
    values = _transition(asset, found)
    if values is not None:
        await VideoRepository(session).update(asset.id, values)
        logger.info(
            "video_status", video_id=str(asset.id), **{k: str(v) for k, v in values.items()}
        )
        return str(values["status"])
    return asset.status


async def process_video(
    sessionmaker: async_sessionmaker[AsyncSession], providers: Providers, video_id: UUID
) -> str | None:
    """Background job (system context): refresh one video's status from its provider."""
    async with system_transaction(sessionmaker) as session:
        asset = await VideoRepository(session).get(video_id)
        if asset is None:
            return None
        provider, key = asset.provider, asset.provider_video_id
        result = await refresh_video(session, providers, asset)
    # A rejected local upload (oversize or not an MP4) can never play: free the object once the
    # failed status is committed. A presigned PUT still valid can upload a replacement.
    if result == VideoStatus.FAILED and provider == "local":
        await providers[provider].delete(key)
    return result


async def handle_bunny_webhook(
    sessionmaker: async_sessionmaker[AsyncSession],
    providers: Providers,
    settings: Settings,
    secret: str,
    body: object,
) -> bool:
    """Bunny's processing webhook. The URL secret is compared in constant time; the body only
    names the video, whose status is re-fetched from the Bunny API. Returns False when the secret
    is wrong (the route answers 404)."""
    expected = settings.bunny_webhook_secret
    if expected is None or "bunny" not in providers:
        return False
    if not hmac.compare_digest(secret.encode(), expected.get_secret_value().encode()):
        return False
    video_id = providers["bunny"].handle_webhook(body)
    if video_id is None:
        return True  # authenticated but not about a video: acknowledge and ignore
    async with system_transaction(sessionmaker) as session:
        asset = await VideoRepository(session).find("bunny", video_id)
        if asset is not None:
            await refresh_video(session, providers, asset)
    return True


# ============================================================================ files (PDF, image)

# Leading bytes each accepted content type must start with (checked on confirm; the browser's
# declared Content-Type alone proves nothing about the bytes).
_MAGIC: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}
_ASCII_NAME = re.compile(r"[^A-Za-z0-9._ -]")


@dataclass(frozen=True, slots=True)
class FileDownload:
    url: str
    file_name: str
    expires_at: datetime


def _max_bytes(settings: Settings, kind: str) -> int:
    if kind == FileKind.SUBMISSION:
        return settings.submission_upload_max_bytes
    return (
        settings.pdf_upload_max_bytes if kind == FileKind.PDF else settings.image_upload_max_bytes
    )


def _matches(content_type: str, head: bytes) -> bool:
    if content_type == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return any(head.startswith(magic) for magic in _MAGIC.get(content_type, ()))


def _file_out(file: StoredFile) -> FileOut:
    return FileOut.model_validate(file)


def _sign_download(storage: ObjectStorage, file: StoredFile, ttl: int) -> FileDownload:
    # The header value must be plain ASCII; the display name keeps the original.
    ascii_name = _ASCII_NAME.sub("_", file.file_name) or file.kind
    url = storage.presigned_get(file.storage_key, expires_in=ttl, download_name=ascii_name)
    return FileDownload(url=url, file_name=file.file_name, expires_at=_expires_at(ttl))


async def _own_file(ctx: RequestContext, file_id: UUID) -> StoredFile:
    org_id = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    file = await FileRepository(ctx.session).get(file_id)
    # Enrolled students may read a file through RLS; managing it is the owner's.
    if file is None or (file.organization_id != org_id and not ctx.principal.is_platform_admin):
        raise NotFoundError("File not found.")
    return file


async def create_file(
    ctx: RequestContext, storage: ObjectStorage, settings: Settings, body: FileCreate
) -> FileUploadOut:
    """A pending file plus a presigned POST that storage restricts to this key, this exact
    Content-Type and the kind's size limit. The browser then calls `POST /files/{id}/confirm`."""
    org_id = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    if (body.kind == "pdf") != (body.content_type == "application/pdf"):
        raise UnprocessableError(
            "PDF files must be application/pdf; images must be PNG, JPEG, WebP or GIF.",
            code="invalid_content_type",
        )
    return await _create(
        ctx, storage, settings, org_id, kind=body.kind, file_name=body.file_name,
        content_type=body.content_type,
    )  # fmt: skip


async def create_submission_upload(
    ctx: RequestContext,
    storage: ObjectStorage,
    settings: Settings,
    *,
    file_name: str,
    content_type: SubmissionContentType,
) -> FileUploadOut:
    """A student's assignment upload (the assignments module has checked the enrollment).
    Same presigned POST rules as editors' files; kind `submission`, readable by the student and
    their org's graders only (RLS, migration 0011)."""
    org_id = require_org(ctx.principal)
    return await _create(
        ctx, storage, settings, org_id, kind=FileKind.SUBMISSION, file_name=file_name,
        content_type=content_type,
    )  # fmt: skip


async def confirm_submission_file(
    ctx: RequestContext, storage: ObjectStorage, settings: Settings, file_id: UUID
) -> FileOut:
    """Confirm the caller's own submission upload (size and leading bytes), like `confirm_file`.
    404 for anything but a submission file the caller uploaded."""
    file = await FileRepository(ctx.session).get(file_id)
    if file is None or file.kind != FileKind.SUBMISSION or file.created_by != ctx.principal.user_id:
        raise NotFoundError("File not found.")
    return await _confirm(ctx, storage, settings, file)


async def _create(
    ctx: RequestContext,
    storage: ObjectStorage,
    settings: Settings,
    org_id: UUID,
    *,
    kind: str,
    file_name: str,
    content_type: str,
) -> FileUploadOut:
    file_id = new_id()
    key = f"files/{org_id}/{file_id}/{kind}"  # never the user's file name
    max_bytes = _max_bytes(settings, kind)
    ttl = settings.file_upload_ttl_seconds
    post = await asyncio.to_thread(
        storage.presigned_post,
        key,
        content_type=content_type,
        max_bytes=max_bytes,
        expires_in=ttl,
    )
    file = await FileRepository(ctx.session).create(
        StoredFile(
            id=file_id, organization_id=org_id, kind=kind, storage_key=key,
            file_name=file_name, content_type=content_type, status=FileStatus.PENDING,
            created_by=ctx.principal.user_id,
        )
    )  # fmt: skip
    await audit.record(
        ctx.session,
        ctx.actor,
        action="file.created",
        target_type="file",
        target_id=file.id,
        after={"kind": kind, "file_name": file_name},
    )
    return FileUploadOut(
        file=_file_out(file),
        upload=PresignedPostOut(
            url=post.url, fields=post.fields, max_bytes=max_bytes, expires_at=_expires_at(ttl)
        ),
    )


async def confirm_file(
    ctx: RequestContext, storage: ObjectStorage, settings: Settings, file_id: UUID
) -> FileOut:
    """Check the uploaded object's size and leading bytes; `ready` on success. A mismatch is
    `422 file_rejected` (the reason in `details`): the rejected status is committed first, so the
    file can never be attached, and the object is deleted. A missing object is `409` and leaves
    the file pending, so the browser can retry. Idempotent."""
    return await _confirm(ctx, storage, settings, await _own_file(ctx, file_id))


async def _confirm(
    ctx: RequestContext, storage: ObjectStorage, settings: Settings, file: StoredFile
) -> FileOut:
    file_id = file.id
    if file.status == FileStatus.READY:
        return _file_out(file)
    if file.status == FileStatus.REJECTED:
        raise _rejected("This upload was rejected. Upload the file again.")
    size = await asyncio.to_thread(storage.size, file.storage_key)
    if size is None:
        raise ConflictError("The upload hasn't arrived yet.", code="upload_not_found")
    head = await asyncio.to_thread(storage.read_range, file.storage_key, 0, 16) if size else b""
    reason: str | None = None
    if not 0 < size <= _max_bytes(settings, file.kind):
        reason = "The file exceeds the size limit."
    elif not _matches(file.content_type, head):
        reason = "The file's contents don't match its type."

    if reason is not None:
        # The request transaction rolls back when we answer 4xx, so record the rejection in its
        # own transaction (same caller, same RLS) and commit it before raising.
        principal = ctx.principal
        async with independent_transaction(
            ctx.session,
            organization_id=principal.organization_id,
            user_id=principal.user_id,
            is_platform_admin=principal.is_platform_admin,
        ) as session:
            await FileRepository(session).update(
                file_id, {"status": FileStatus.REJECTED, "size_bytes": size}
            )
            await audit.record(
                session, ctx.actor, action="file.rejected", target_type="file",
                target_id=file_id, after={"size_bytes": size, "reason": reason},
            )  # fmt: skip
        await asyncio.to_thread(storage.delete, file.storage_key)
        raise _rejected(reason)

    repo = FileRepository(ctx.session)
    await repo.update(file_id, {"status": FileStatus.READY, "size_bytes": size})
    await audit.record(
        ctx.session, ctx.actor, action="file.confirmed", target_type="file", target_id=file_id,
        after={"size_bytes": size},
    )  # fmt: skip
    refreshed = await repo.get(file_id)
    assert refreshed is not None  # noqa: S101
    return _file_out(refreshed)


def _rejected(reason: str) -> UnprocessableError:
    return UnprocessableError(
        "The upload was rejected.", code="file_rejected", details={"reason": reason}
    )


async def get_file(ctx: RequestContext, file_id: UUID) -> FileOut:
    return _file_out(await _own_file(ctx, file_id))


async def list_files(
    ctx: RequestContext, params: CursorParams, kind: str | None
) -> tuple[list[FileOut], str | None]:
    org_id = require_org_permission(ctx.principal, Permission.COURSE_EDIT)
    rows, cursor = await FileRepository(ctx.session).list_page(org_id, params, kind=kind)
    return [_file_out(f) for f in rows], cursor


async def editor_download(
    ctx: RequestContext, storage: ObjectStorage, settings: Settings, file_id: UUID
) -> FileDownloadOut:
    file = await _own_file(ctx, file_id)
    if file.status != FileStatus.READY:
        raise ConflictError("The file isn't confirmed yet.", code="file_not_ready")
    signed = _sign_download(storage, file, settings.file_download_ttl_seconds)
    return FileDownloadOut(url=signed.url, file_name=signed.file_name, expires_at=signed.expires_at)


async def download_urls(
    session: AsyncSession, storage: ObjectStorage, ids: Sequence[UUID], ttl_seconds: int
) -> dict[UUID, FileDownload]:
    """Signed, short-lived download URLs for ready files the caller may read (RLS: editors of the
    owner org, or students enrolled in a version that uses the file). Others are absent."""
    files = await FileRepository(session).get_many(list(ids))
    return {
        f.id: _sign_download(storage, f, ttl_seconds) for f in files if f.status == FileStatus.READY
    }
