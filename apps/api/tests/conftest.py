"""Test fixtures. Tests run against the real Postgres and Redis from docker-compose / CI services.

A dedicated `<POSTGRES_DB>_test` database is recreated once per session and migrated to head with
the owner role; the app and `db_session` then connect as the non-owner runtime role, exactly as in
production, so RLS and grants are exercised for real.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.main import create_app

API_ROOT = Path(__file__).resolve().parents[1]


def _with_database(url: SecretStr, database: str) -> SecretStr:
    return SecretStr(
        make_url(url.get_secret_value())
        .set(database=database)
        .render_as_string(hide_password=False)
    )


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.attributes["database_url"] = database_url
    return cfg


@pytest.fixture(scope="session")
def settings() -> Settings:
    base = Settings()
    test_db = f"{make_url(base.database_url.get_secret_value()).database}_test"
    return base.model_copy(
        update={
            "environment": "test",
            "database_url": _with_database(base.database_url, test_db),
            "migration_database_url": _with_database(base.migration_database_url, test_db),
        }
    )


async def recreate_database(owner_url: str) -> None:
    url = make_url(owner_url)
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            # Identifiers cannot be bound parameters; the name comes from our own settings.
            name = url.database
            assert name is not None
            assert name.replace("_", "").isalnum(), name
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
def migrated_database(settings: Settings) -> None:
    owner_url = settings.migration_database_url.get_secret_value()
    asyncio.run(recreate_database(owner_url))
    command.upgrade(alembic_config(owner_url), "head")


@pytest.fixture(scope="session")
async def app(settings: Settings, migrated_database: None) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db_session(app: FastAPI) -> AsyncIterator[AsyncSession]:
    """A session as the runtime role inside a transaction that is always rolled back."""
    async with app.state.sessionmaker() as session:
        await session.begin()
        try:
            yield session
        finally:
            await session.rollback()
