"""Celery tasks for the identity module (autodiscovered by app.worker)."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.storage import ObjectStorage
from app.db.session import create_sessionmaker
from app.db.tenancy import set_tenant_context
from app.modules.identity import service
from app.modules.identity.imports import run_import
from app.modules.identity.keycloak_admin import KeycloakAdmin
from app.worker import celery_app

PROCESS_IMPORT = "identity.process_import"
EXPIRE_INVITATIONS = "identity.expire_invitations"


def enqueue_import(job_id: UUID, org_id: UUID, user_id: UUID) -> None:
    celery_app.send_task(PROCESS_IMPORT, args=[str(job_id), str(org_id), str(user_id)])


async def _process_import(job_id: UUID, org_id: UUID, user_id: UUID) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url.get_secret_value(), poolclass=NullPool)
    try:
        async with httpx.AsyncClient() as http:
            await run_import(
                sessionmaker=create_sessionmaker(engine),
                storage=ObjectStorage(settings),
                idp=KeycloakAdmin(http, settings),
                job_id=job_id,
                org_id=org_id,
                user_id=user_id,
                chunk_size=settings.import_chunk_size,
                max_rows=settings.import_max_rows,
            )
    finally:
        await engine.dispose()


@celery_app.task(name=PROCESS_IMPORT)
def process_import(job_id: str, org_id: str, user_id: str) -> None:
    asyncio.run(_process_import(UUID(job_id), UUID(org_id), UUID(user_id)))


async def _expire_invitations() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url.get_secret_value(), poolclass=NullPool)
    try:
        async with create_sessionmaker(engine)() as session, session.begin():
            # A system job across all orgs: platform-admin RLS context, no acting user.
            await set_tenant_context(
                session, organization_id=None, user_id=None, is_platform_admin=True
            )
            return await service.expire_invitations(session, datetime.now(UTC))
    finally:
        await engine.dispose()


@celery_app.task(name=EXPIRE_INVITATIONS)
def expire_invitations() -> int:
    return asyncio.run(_expire_invitations())
