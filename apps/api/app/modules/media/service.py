"""Media module public interface.

Videos: an editor creates an asset and gets an upload ticket for the configured provider; the
browser uploads directly to the provider (never through our API); the asset becomes `ready` when
processing finishes (a Celery task for the local provider, the Bunny webhook in production).
Playback URLs are short-lived and signed; students get them only for videos in courses they can
read (RLS on `video_assets`, migration 0006).
"""

import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.pagination import CursorParams
from app.db.base import new_id
from app.db.session import run_after_commit
from app.db.tenancy import system_transaction
from app.modules.audit import service as audit
from app.modules.identity.authz import Permission, require_org_permission
from app.modules.identity.dependencies import RequestContext
from app.modules.media.models import FileStatus, StoredFile, VideoAsset, VideoStatus
from app.modules.media.providers import (
    Playback,
    ProviderStatus,
    VideoProvider,
)
from app.modules.media.repository import VideoRepository
from app.modules.media.schemas import PlaybackOut as PlaybackOut  # noqa: PLC0414 - public interface
from app.modules.media.schemas import UploadTicketOut, VideoOut, VideoUploadOut

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
        select(StoredFile.id, StoredFile.organization_id, StoredFile.kind, StoredFile.status).where(
            StoredFile.id.in_(ids)
        )
    )
    return {r.id: FileInfo(r.id, r.organization_id, r.kind, r.status) for r in rows}


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
