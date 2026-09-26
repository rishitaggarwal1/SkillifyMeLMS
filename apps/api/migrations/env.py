"""Alembic environment (async). Migrations run as the schema-owner role (MIGRATION_DATABASE_URL);
the API itself connects as the non-owner application role so that RLS applies to it."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.models import Base
from app.db.types import LTree

# Let autogenerate reflect ltree columns (skills.path) instead of warning about an unknown type.
ischema_names.setdefault("ltree", LTree)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Schema objects autogenerate cannot represent faithfully; managed by hand in migrations.
# fk_import_jobs_batch_org uses `ON DELETE SET NULL (batch_id)` (PG15+ column list).
_UNMANAGED = frozenset({"fk_import_jobs_batch_org"})


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    return name not in _UNMANAGED


def _database_url() -> str:
    # Tests (and tooling) can point Alembic at another database via `-x url=...` or attributes.
    url = config.attributes.get("database_url") or context.get_x_argument(as_dictionary=True).get(
        "url"
    )
    if url:
        return str(url)
    return get_settings().migration_database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
