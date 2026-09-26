"""Schema-wide guards for the rules in CLAUDE.md. These scan the migrated test database, so they
also cover every table future phases add."""

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from tests.fixtures import alembic_config

# Tables that deliberately have no RLS (none today). Add with a justification if ever needed.
RLS_EXEMPT = {"alembic_version"}


async def test_every_table_has_row_level_security(db_session: AsyncSession) -> None:
    rows = await db_session.execute(
        text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity"
        )
    )
    assert {r.relname for r in rows} - RLS_EXEMPT == set()


async def test_policies_are_written_per_operation(db_session: AsyncSession) -> None:
    rows = await db_session.execute(
        text("SELECT tablename, policyname FROM pg_policies WHERE cmd = 'ALL'")
    )
    assert [tuple(r) for r in rows] == []


async def test_every_foreign_key_is_indexed(db_session: AsyncSession) -> None:
    # A FK is covered if some index's leading columns are exactly the FK's columns (any order
    # within that prefix is not accepted; we want the FK columns first).
    rows = await db_session.execute(
        text(
            """
            SELECT c.conrelid::regclass AS table_name, c.conname
            FROM pg_constraint c
            WHERE c.contype = 'f'
              AND c.connamespace = 'public'::regnamespace
              AND NOT EXISTS (
                SELECT 1 FROM pg_index i
                WHERE i.indrelid = c.conrelid
                  AND (i.indkey::int2[])[0:cardinality(c.conkey) - 1] @> c.conkey
                  AND (i.indkey::int2[])[0:cardinality(c.conkey) - 1] <@ c.conkey
              )
            """
        )
    )
    assert [f"{r.table_name}.{r.conname}" for r in rows] == []


async def test_phase2_prerequisites_exist(db_session: AsyncSession) -> None:
    publisher_col = (
        await db_session.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'organizations' AND column_name = 'is_content_publisher'"
            )
        )
    ).one()
    batch_uq = await db_session.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'uq_batches_id_organization_id'"
        )
    )

    assert publisher_col.is_nullable == "NO"
    assert publisher_col.column_default == "false"
    assert batch_uq == "UNIQUE (id, organization_id)"


def test_models_match_migrations(settings: Settings, migrated_database: None) -> None:
    # Fails if a model changed without a migration (or vice versa).
    command.check(alembic_config(settings.migration_database_url.get_secret_value()))
