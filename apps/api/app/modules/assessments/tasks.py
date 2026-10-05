"""Durable quiz expiry; every finalization has its own explicit system transaction."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.session import create_sessionmaker
from app.db.tenancy import system_transaction
from app.modules.assessments import service
from app.modules.assessments.repository import RuntimeRepository
from app.worker import celery_app


@asynccontextmanager
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(get_settings().database_url.get_secret_value(), poolclass=NullPool)
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


async def run_expiry(
    sessionmaker: async_sessionmaker[AsyncSession], attempt: UUID, org: UUID
) -> datetime | None:
    async with system_transaction(sessionmaker, organization_id=org) as session:
        return await service.finalize_due(session, attempt)


async def sweep(sessionmaker: async_sessionmaker[AsyncSession], limit: int = 500) -> int:
    async with system_transaction(sessionmaker) as session:
        due = await RuntimeRepository(session).due(limit)
    for attempt, org in due:
        await run_expiry(sessionmaker, attempt, org)
    return len(due)


async def _expire(attempt: str, org: str) -> datetime | None:
    async with session_factory() as sessionmaker:
        return await run_expiry(sessionmaker, UUID(attempt), UUID(org))


@celery_app.task(name="assessments.expire_attempt")
def expire_attempt(attempt: str, org: str) -> None:
    deadline = asyncio.run(_expire(attempt, org))
    if deadline is not None:
        expire_attempt.apply_async(args=(attempt, org), eta=deadline)


async def _sweep() -> int:
    async with session_factory() as sessionmaker:
        return await sweep(sessionmaker)


@celery_app.task(name="assessments.sweep_expired")
def sweep_expired() -> int:
    return asyncio.run(_sweep())
