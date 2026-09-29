"""Data access for media tables (RLS: owner-org editors; readers of a course see the videos its
published versions use)."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorParams, paginate_by_id
from app.modules.media.models import VideoAsset


class VideoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, video_id: UUID) -> VideoAsset | None:
        return await self.session.get(VideoAsset, video_id, populate_existing=True)

    async def find(self, provider: str, provider_video_id: str) -> VideoAsset | None:
        return await self.session.scalar(
            select(VideoAsset).where(
                VideoAsset.provider == provider, VideoAsset.provider_video_id == provider_video_id
            )
        )

    async def list_page(
        self, organization_id: UUID, params: CursorParams, *, status: str | None
    ) -> tuple[list[VideoAsset], str | None]:
        stmt = select(VideoAsset).where(VideoAsset.organization_id == organization_id)
        if status is not None:
            stmt = stmt.where(VideoAsset.status == status)
        return await paginate_by_id(self.session, stmt, VideoAsset.id, params)

    async def create(self, asset: VideoAsset) -> VideoAsset:
        self.session.add(asset)
        await self.session.flush()
        await self.session.refresh(asset)
        return asset

    async def update(self, video_id: UUID, values: dict[str, Any]) -> None:
        await self.session.execute(
            update(VideoAsset)
            .where(VideoAsset.id == video_id)
            .values(**values, updated_at=func.now())
        )
