"""Media module public interface.

Step 2 exposes what publishing needs: the status of the videos and files a course's lessons point
at. Uploads, the video providers and signed URLs arrive with the video and PDF steps.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.media.models import FileStatus, StoredFile, VideoAsset, VideoStatus


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


async def videos(session: AsyncSession, ids: Sequence[UUID]) -> dict[UUID, VideoInfo]:
    """Video assets visible to the caller (RLS: the owner org's course editors)."""
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
