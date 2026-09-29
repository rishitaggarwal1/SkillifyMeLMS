"""Media HTTP API: video and file (PDF, image) uploads and previews for course editors, and the
video provider webhook."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.core.pagination import CursorPage, PageParams
from app.core.storage import ObjectStorage
from app.modules.identity.dependencies import RequestCtx
from app.modules.media import service
from app.modules.media.schemas import (
    FileCreate,
    FileDownloadOut,
    FileKindName,
    FileOut,
    FileUploadOut,
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


def get_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


Providers = Annotated[service.Providers, Depends(get_providers)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]
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


files = APIRouter(prefix="/files", tags=["media"])


@files.post("", status_code=status.HTTP_201_CREATED, operation_id="create_file")
async def create_file(
    ctx: RequestCtx, storage: Storage, settings: AppSettings, body: FileCreate
) -> FileUploadOut:
    """Create a pending PDF or image and get a presigned POST: send `upload.fields` as form
    fields and the file last, straight to storage. Then call `POST /files/{id}/confirm`."""
    return await service.create_file(ctx, storage, settings, body)


@files.get("", operation_id="list_files")
async def list_files(
    ctx: RequestCtx, page: PageParams, kind: FileKindName | None = None
) -> CursorPage[FileOut]:
    items, cursor = await service.list_files(ctx, page, kind)
    return CursorPage(items=items, next_cursor=cursor)


@files.get("/{file_id}", operation_id="get_file")
async def get_file(ctx: RequestCtx, file_id: UUID) -> FileOut:
    return await service.get_file(ctx, file_id)


@files.post("/{file_id}/confirm", operation_id="confirm_file")
async def confirm_file(
    ctx: RequestCtx, storage: Storage, settings: AppSettings, file_id: UUID
) -> FileOut:
    """Check the upload's size and contents (a PDF must start with `%PDF-`): the file becomes
    `ready` to attach to lessons. Otherwise `422 file_rejected` with the reason in `details`; the
    file stays rejected and the object is deleted."""
    return await service.confirm_file(ctx, storage, settings, file_id)


@files.get("/{file_id}/download", operation_id="get_file_download")
async def download(
    ctx: RequestCtx, storage: Storage, settings: AppSettings, file_id: UUID
) -> FileDownloadOut:
    """A signed download URL (a few minutes) for the course editors."""
    return await service.editor_download(ctx, storage, settings, file_id)


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
router.include_router(files)
router.include_router(webhooks)
