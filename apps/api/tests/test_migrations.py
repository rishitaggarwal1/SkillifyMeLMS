import asyncio

from alembic import command
from sqlalchemy import make_url

from app.core.config import Settings
from tests.fixtures import alembic_config, recreate_database


def test_migrations_upgrade_downgrade_upgrade(settings: Settings, migrated_database: None) -> None:
    # A scratch database, so the round trip cannot disturb the shared test database or its pool.
    url = make_url(settings.migration_database_url.get_secret_value())
    scratch = url.set(database=f"{url.database}_migrations").render_as_string(hide_password=False)
    asyncio.run(recreate_database(scratch))
    cfg = alembic_config(scratch)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
