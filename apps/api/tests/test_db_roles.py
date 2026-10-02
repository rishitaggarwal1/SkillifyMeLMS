"""`python -m app.cli.db_roles`: Keycloak's own login role and database (servers)."""

from uuid import uuid4

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.cli.db_roles import ensure_owned_database, ensure_roles
from app.core.config import Settings
from app.db.rls import APP_ROLE


async def test_keycloak_database_is_owned_by_its_own_role_and_closed_to_others(
    settings: Settings, migrated_database: None
) -> None:
    suffix = uuid4().hex[:8]
    role, database = f"kc_role_{suffix}", f"kc_db_{suffix}"
    owner_url = (
        make_url(settings.migration_database_url.get_secret_value())
        .set(database="postgres")
        .render_as_string(hide_password=False)
    )
    engine = create_async_engine(owner_url, isolation_level="AUTOCOMMIT")
    try:
        for _ in range(2):  # idempotent
            await ensure_roles(owner_url, {role: f"pw-{uuid4().hex}"})
            await ensure_owned_database(owner_url, database, role)
        async with engine.connect() as conn:
            owner = await conn.scalar(
                text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = :name"),
                {"name": database},
            )
            app_can_connect = await conn.scalar(
                text("SELECT has_database_privilege(:role, :name, 'CONNECT')"),
                {"role": APP_ROLE, "name": database},
            )
            attrs = (
                await conn.execute(
                    text(
                        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                        "FROM pg_roles WHERE rolname = :role"
                    ),
                    {"role": role},
                )
            ).one()
        assert owner == role
        assert app_can_connect is False
        assert tuple(attrs) == (False, False, False, False)
    finally:
        async with engine.connect() as conn:
            await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database}"')
            await conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
        await engine.dispose()
