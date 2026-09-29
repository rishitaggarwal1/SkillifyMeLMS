"""Data access for media tables (RLS: owner-org editors; enrolled students see the videos and
files their course version uses)."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorParams, paginate_by_id
from app.modules.media.models import StoredFile, VideoAsset


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


class FileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, file_id: UUID) -> StoredFile | None:
        return await self.session.get(StoredFile, file_id, populate_existing=True)

    async def get_many(self, ids: list[UUID]) -> list[StoredFile]:
        if not ids:
            return []
        return list(await self.session.scalars(select(StoredFile).where(StoredFile.id.in_(ids))))

    async def list_page(
        self, organization_id: UUID, params: CursorParams, *, kind: str | None
    ) -> tuple[list[StoredFile], str | None]:
        stmt = select(StoredFile).where(StoredFile.organization_id == organization_id)
        if kind is not None:
            stmt = stmt.where(StoredFile.kind == kind)
        return await paginate_by_id(self.session, stmt, StoredFile.id, params)

    async def create(self, file: StoredFile) -> StoredFile:
        self.session.add(file)
        await self.session.flush()
        await self.session.refresh(file)
        return file

    async def update(self, file_id: UUID, values: dict[str, Any]) -> None:
        await self.session.execute(
            update(StoredFile)
            .where(StoredFile.id == file_id)
            .values(**values, updated_at=func.now())
        )
