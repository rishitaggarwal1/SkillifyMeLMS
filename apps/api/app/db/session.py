"""Async engine/session factory and the per-request session dependency."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "application_name": settings.service_name,
                "statement_timeout": str(settings.db_statement_timeout_ms),
                "timezone": "UTC",
            },
        },
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request: commit on success, roll back on any exception.

    Tenant context (app.current_org / app.current_user) is transaction-scoped, so it must be set
    inside this transaction via `app.db.tenancy.set_tenant_context`.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        async with session.begin():
            yield session
        # Committed: run async follow-ups (cache invalidation...) registered during the request.
        for callback in session.info.pop(_ASYNC_AFTER_COMMIT_KEY, []):
            await callback()


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


# ---------------------------------------------------------------------------- after-commit hooks
# Side effects that must only happen once the transaction is durable (enqueueing a Celery job that
# reads the rows we just wrote, cache invalidation...). Callbacks are dropped on rollback.

_AFTER_COMMIT_KEY = "after_commit_callbacks"
_ASYNC_AFTER_COMMIT_KEY = "async_after_commit_callbacks"


def run_after_commit(session: AsyncSession, callback: Callable[[], object]) -> None:
    session.sync_session.info.setdefault(_AFTER_COMMIT_KEY, []).append(callback)


def run_after_commit_async(
    session: AsyncSession, callback: Callable[[], Awaitable[object]]
) -> None:
    """Await `callback` after the request transaction commits (request sessions only)."""
    session.info.setdefault(_ASYNC_AFTER_COMMIT_KEY, []).append(callback)


@event.listens_for(Session, "after_commit")
def _run_after_commit_callbacks(session: Session) -> None:
    callbacks = session.info.pop(_AFTER_COMMIT_KEY, [])
    for callback in callbacks:
        callback()


@event.listens_for(Session, "after_rollback")
def _drop_after_commit_callbacks(session: Session) -> None:
    session.info.pop(_AFTER_COMMIT_KEY, None)
    session.info.pop(_ASYNC_AFTER_COMMIT_KEY, None)
