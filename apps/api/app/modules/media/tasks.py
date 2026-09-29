"""Celery tasks for the media module (autodiscovered by app.worker)."""

import asyncio
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.storage import ObjectStorage
from app.db.session import create_sessionmaker
from app.modules.media import service
from app.modules.media.providers import create_providers
from app.worker import celery_app


async def _process(video_id: UUID) -> str | None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url.get_secret_value(), poolclass=NullPool)
    try:
        async with httpx.AsyncClient() as http:
            providers = create_providers(settings, ObjectStorage(settings), http)
            return await service.process_video(create_sessionmaker(engine), providers, video_id)
    finally:
        await engine.dispose()


@celery_app.task(name=service.PROCESS_VIDEO)
def process_video(video_id: str) -> str | None:
    return asyncio.run(_process(UUID(video_id)))
