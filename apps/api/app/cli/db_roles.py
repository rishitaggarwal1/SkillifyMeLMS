"""Create or update the database login roles the platform uses. Safe to run repeatedly.

    python -m app.cli.db_roles

Runs as the owner (MIGRATION_DATABASE_URL) before migrations, in docker-compose and CI. Production
roles are managed by Terraform instead. Neither role is a superuser, owns tables, or can bypass RLS:

- skillify_app    the API and Celery workers (RLS always applies)
- skillify_relay  the outbox relay (may only read outbox_events and set published_at)

Passwords come from APP_DB_PASSWORD / RELAY_DB_PASSWORD. They are passed to Postgres as bound
parameters and quoted server-side with format(%L), never interpolated client-side.
"""

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.rls import APP_ROLE, RELAY_ROLE

_ENSURE_ROLE = text(
    """
    SELECT CASE
      WHEN EXISTS (SELECT FROM pg_roles WHERE rolname = CAST(:role AS text))
      THEN format('ALTER ROLE %I ' || :attrs || ' PASSWORD %L',
                  CAST(:role AS text), CAST(:password AS text))
      ELSE format('CREATE ROLE %I ' || :attrs || ' PASSWORD %L',
                  CAST(:role AS text), CAST(:password AS text))
    END
    """
)


# Fixed, non-privileged attributes for every managed role (a constant, not user input).
_ROLE_ATTRS = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"


async def ensure_roles(owner_url: str, passwords: dict[str, str]) -> None:
    engine = create_async_engine(owner_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for role, password in passwords.items():
                ddl = await conn.scalar(
                    _ENSURE_ROLE, {"role": role, "password": password, "attrs": _ROLE_ATTRS}
                )
                # The statement text was produced by Postgres' own format(%I, %L) quoting.
                await conn.exec_driver_sql(str(ddl))
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    if settings.app_db_password is None or settings.relay_db_password is None:
        sys.exit("APP_DB_PASSWORD and RELAY_DB_PASSWORD must be set")
    passwords = {
        APP_ROLE: settings.app_db_password.get_secret_value(),
        RELAY_ROLE: settings.relay_db_password.get_secret_value(),
    }
    owner_url = settings.migration_database_url.get_secret_value()
    asyncio.run(ensure_roles(owner_url, passwords))
    print(f"Ensured database roles: {', '.join(passwords)}")  # noqa: T201


if __name__ == "__main__":
    main()
