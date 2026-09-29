"""Celery tasks for the enrollments module (autodiscovered by app.worker). Both are idempotent."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.redis import create_redis
from app.db.session import create_sessionmaker
from app.db.tenancy import system_transaction
from app.modules.enrollments import service
from app.modules.enrollments.jobs import RECONCILE_COURSE_ORG, UPGRADE_ENROLLMENTS
from app.modules.enrollments.video_flush import flush_video_progress
from app.worker import celery_app

FLUSH_BATCH = 500


async def _flush() -> int:
    redis = create_redis(get_settings())
    try:
        async with _sessionmaker() as sessionmaker:
            total = 0
            # Drain batches; do not limit throughput to one batch per beat tick.
            for _ in range(100):
                result = await flush_video_progress(sessionmaker, redis, limit=FLUSH_BATCH)
                total += result.changed
                # Stop on a short sample, not on few changes: entries skipped as unchanged
                # still count, or a batch of them would end the drain with work left.
                if result.sampled < FLUSH_BATCH:
                    break
            else:
                celery_app.send_task("enrollments.flush_video_progress")
            return total
    finally:
        await redis.aclose()


@celery_app.task(name="enrollments.flush_video_progress")
def flush_progress() -> int:
    return asyncio.run(_flush())


@asynccontextmanager
async def _sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url.get_secret_value(), poolclass=NullPool)
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


async def run_reconcile_course_org(
    sessionmaker: async_sessionmaker[AsyncSession], course_id: UUID, organization_id: UUID
) -> service.ReconcileResult:
    async with system_transaction(sessionmaker, organization_id=organization_id) as session:
        return await service.reconcile_course_org(session, course_id, organization_id)


def parse_upgrade_args(
    course_id: str, organization_id: str, user_id: str, to_major: str, batch_ids: str
) -> dict[str, object]:
    return {
        "course_id": UUID(course_id),
        "organization_id": UUID(organization_id),
        "user_id": UUID(user_id),
        "to_major": int(to_major),
        "batch_ids": [UUID(b) for b in batch_ids.split(",") if b],
    }


async def _reconcile(course_id: str, organization_id: str) -> None:
    async with _sessionmaker() as sessionmaker:
        await run_reconcile_course_org(sessionmaker, UUID(course_id), UUID(organization_id))


async def _upgrade(*args: str) -> int:
    async with _sessionmaker() as sessionmaker:
        return await service.run_upgrade(sessionmaker, **parse_upgrade_args(*args))  # type: ignore[arg-type]


@celery_app.task(name=RECONCILE_COURSE_ORG)
def reconcile_course_org(course_id: str, organization_id: str) -> None:
    asyncio.run(_reconcile(course_id, organization_id))


@celery_app.task(name=UPGRADE_ENROLLMENTS)
def upgrade_enrollments(
    course_id: str, organization_id: str, user_id: str, to_major: str, batch_ids: str
) -> int:
    return asyncio.run(_upgrade(course_id, organization_id, user_id, to_major, batch_ids))
