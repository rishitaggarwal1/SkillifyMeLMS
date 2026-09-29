"""Media HTTP API: video uploads and previews for course editors, and the provider webhook."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.core.pagination import CursorPage, PageParams
from app.modules.identity.dependencies import RequestCtx
from app.modules.media import service
from app.modules.media.schemas import (
    PlaybackOut,
    VideoCreate,
    VideoOut,
    VideoStatusName,
    VideoUploadOut,
)

router = APIRouter()


def get_providers(request: Request) -> service.Providers:
    providers: service.Providers = request.app.state.video_providers
    return providers


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


Providers = Annotated[service.Providers, Depends(get_providers)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]

videos = APIRouter(prefix="/videos", tags=["media"])


@videos.post("", status_code=status.HTTP_201_CREATED, operation_id="create_video")
async def create_video(
    ctx: RequestCtx, providers: Providers, settings: AppSettings, body: VideoCreate
) -> VideoUploadOut:
    """Create a video and get an upload ticket; the browser uploads directly to storage, then
    calls `POST /videos/{id}/uploaded`."""
    return await service.create_video(ctx, providers, settings, body.title)


@videos.get("", operation_id="list_videos")
async def list_videos(
    ctx: RequestCtx,
    page: PageParams,
    status_: Annotated[VideoStatusName | None, Query(alias="status")] = None,
) -> CursorPage[VideoOut]:
    items, cursor = await service.list_videos(ctx, page, status_)
    return CursorPage(items=items, next_cursor=cursor)


@videos.get("/{video_id}", operation_id="get_video")
async def get_video(ctx: RequestCtx, video_id: UUID) -> VideoOut:
    """Poll this for processing status."""
    return await service.get_video(ctx, video_id)


@videos.post("/{video_id}/uploaded", operation_id="mark_video_uploaded")
async def mark_uploaded(ctx: RequestCtx, video_id: UUID) -> VideoOut:
    return await service.mark_uploaded(ctx, video_id)


@videos.get("/{video_id}/playback", operation_id="get_video_preview")
async def preview(
    ctx: RequestCtx, providers: Providers, settings: AppSettings, video_id: UUID
) -> PlaybackOut:
    """A signed playback URL for the course editors' preview."""
    return await service.editor_playback(ctx, providers, settings, video_id)


webhooks = APIRouter(prefix="/webhooks", include_in_schema=False)


@webhooks.post("/video/bunny/{secret}")
async def bunny_webhook(
    request: Request, providers: Providers, settings: AppSettings, secret: str
) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = None
    handled = await service.handle_bunny_webhook(
        request.app.state.sessionmaker, providers, settings, secret, body
    )
    if not handled:
        raise NotFoundError
    return {"ok": True}


router.include_router(videos)
router.include_router(webhooks)
