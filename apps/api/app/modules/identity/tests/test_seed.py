"""`make seed` against the test database and the real dev-realm Keycloak."""

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli.seed import seed
from app.core.config import Settings
from app.modules.identity.keycloak_admin import KeycloakAdmin

SNAPSHOT = text(
    """
    SELECT
      (SELECT count(*) FROM organizations WHERE slug IN
         ('skillifyme', 'demo-college', 'other-college')) AS orgs,
      (SELECT count(*) FROM batches b JOIN organizations o ON o.id = b.organization_id
         WHERE o.slug IN ('skillifyme', 'demo-college', 'other-college')) AS batches,
      (SELECT count(*) FROM memberships m JOIN users u ON u.id = m.user_id
         WHERE u.email LIKE '%.local') AS memberships,
      (SELECT count(*) FROM batch_members bm JOIN users u ON u.id = bm.user_id
         WHERE u.email LIKE '%.local') AS batch_members
    """
)


async def test_seed_is_idempotent_and_matches_keycloak(
    settings: Settings,
    migrated_database: None,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with httpx.AsyncClient() as http:
        admin = KeycloakAdmin(http, settings)
        first = await seed(settings, admin)
        async with owner_sessionmaker() as s:
            after_first = (await s.execute(SNAPSHOT)).one()
        await seed(settings, admin)
        async with owner_sessionmaker() as s:
            after_second = (await s.execute(SNAPSHOT)).one()
            multi_orgs = set(
                await s.scalars(
                    text(
                        "SELECT o.slug FROM memberships m JOIN users u ON u.id = m.user_id "
                        "JOIN organizations o ON o.id = m.organization_id "
                        "WHERE u.email = 'multi@skillifyme.local'"
                    )
                )
            )
            cse_batches = set(
                await s.scalars(
                    text(
                        "SELECT b.name FROM batch_members bm JOIN users u ON u.id = bm.user_id "
                        "JOIN batches b ON b.id = bm.batch_id "
                        "WHERE u.email = 'cse.student@demo-college.local'"
                    )
                )
            )
            publisher = await s.scalar(
                text("SELECT is_content_publisher FROM organizations WHERE slug = 'skillifyme'")
            )
        kc_student = await admin.find_user_by_username("cse.student@demo-college.local")
        async with owner_sessionmaker() as s:
            sub = await s.scalar(
                text(
                    "SELECT keycloak_sub FROM users WHERE email = 'cse.student@demo-college.local'"
                )
            )

    assert tuple(after_first) == tuple(after_second) == (3, 3, 12, 3)
    assert len(first.users) == 12
    assert multi_orgs == {"skillifyme", "demo-college"}
    assert cse_batches == {"CSE 2026"}
    assert publisher is True
    assert kc_student is not None
    assert sub == kc_student["id"]  # matches the `sub` of real tokens
