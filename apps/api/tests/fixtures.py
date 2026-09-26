"""Shared pytest fixtures, loaded as a plugin by the root `conftest.py` so that both `tests/` and
`app/modules/*/tests/` can use them.

Tests run against the real Postgres and Redis from docker-compose (Redpanda too, for relay tests).
A dedicated `<POSTGRES_DB>_test` database is recreated once per session and migrated to head with
the owner role; the app, `db_session` and `tenant_session` connect as the non-owner runtime role,
exactly as in production, so RLS and grants are exercised for real. Test data is created through
`factory`, which uses the owner role (bypassing RLS) and commits, so other sessions can see it.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli.db_roles import ensure_roles
from app.core.config import Settings
from app.db.rls import APP_ROLE, RELAY_ROLE
from app.db.tenancy import set_tenant_context
from app.main import create_app
from app.modules.identity.models import User
from tests.auth import SigningKey, StaticJwksSource, TokenFactory, make_validator
from tests.factories import Factory

API_ROOT = Path(__file__).resolve().parents[1]

TenantSessionFactory = Callable[..., AbstractAsyncContextManager[AsyncSession]]
AuthHeaders = Callable[..., dict[str, str]]
TEST_REDIS_DB = 15


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
    redis_url = make_url(base.redis_url.get_secret_value()).set(database=str(TEST_REDIS_DB))
    update: dict[str, object] = {
        "environment": "test",
        "database_url": _with_database(base.database_url, test_db),
        "migration_database_url": _with_database(base.migration_database_url, test_db),
        # A separate Redis DB so cached principals / rate-limit counters never mix with dev data.
        "redis_url": SecretStr(redis_url.render_as_string(hide_password=False)),
    }
    if base.relay_database_url is not None:
        update["relay_database_url"] = _with_database(base.relay_database_url, test_db)
    return base.model_copy(update=update)


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
    if settings.app_db_password is not None and settings.relay_db_password is not None:
        # Roles are cluster-wide; make sure they exist (fresh CI databases, old dev volumes).
        asyncio.run(
            ensure_roles(
                owner_url,
                {
                    APP_ROLE: settings.app_db_password.get_secret_value(),
                    RELAY_ROLE: settings.relay_db_password.get_secret_value(),
                },
            )
        )
    asyncio.run(recreate_database(owner_url))
    command.upgrade(alembic_config(owner_url), "head")


@pytest.fixture(scope="session")
def signing_key() -> SigningKey:
    return SigningKey(kid="test-key-1")


@pytest.fixture(scope="session")
def token_factory(settings: Settings, signing_key: SigningKey) -> TokenFactory:
    return TokenFactory(key=signing_key, issuer=settings.oidc_issuer)


@pytest.fixture(scope="session")
async def app(
    settings: Settings, migrated_database: None, signing_key: SigningKey
) -> AsyncIterator[FastAPI]:
    """The API with a JWT validator trusting the test signing key (same issuer/audience rules as
    production). Tests against real Keycloak build their own app/validator."""
    application = create_app(settings)
    async with LifespanManager(application):
        await application.state.redis.flushdb()
        application.state.jwt_validator = make_validator(settings, StaticJwksSource(signing_key))
        yield application


@pytest.fixture
def auth_headers(token_factory: TokenFactory) -> AuthHeaders:
    """Headers for a request as `user` (optionally in `org`, optionally as platform admin)."""

    def _headers(
        user: User, *, org: UUID | None = None, platform_admin: bool = False, **claims: Any
    ) -> dict[str, str]:
        token = token_factory.token(
            sub=user.keycloak_sub,
            email=user.email,
            name=user.full_name,
            realm_access={"roles": ["platform_admin"] if platform_admin else []},
            **claims,
        )
        headers = {"Authorization": f"Bearer {token}"}
        if org is not None:
            headers["X-Organization-Id"] = str(org)
        return headers

    return _headers


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


@pytest.fixture
def tenant_session(app: FastAPI) -> TenantSessionFactory:
    """Open a runtime-role session acting as a given user in a given org (rolled back on exit):

    async with tenant_session(org=org.id, user=user.id) as s: ...
    """
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    @asynccontextmanager
    async def _open(
        *, org: UUID | None, user: UUID | None, platform_admin: bool = False
    ) -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            await session.begin()
            try:
                await set_tenant_context(
                    session, organization_id=org, user_id=user, is_platform_admin=platform_admin
                )
                yield session
            finally:
                await session.rollback()

    return _open


@pytest.fixture(scope="session")
async def owner_sessionmaker(
    settings: Settings, migrated_database: None
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.migration_database_url.get_secret_value())
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def factory(owner_sessionmaker: async_sessionmaker[AsyncSession]) -> Factory:
    return Factory(owner_sessionmaker)
