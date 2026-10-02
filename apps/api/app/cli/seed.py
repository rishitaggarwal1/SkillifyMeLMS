"""Seed local/dev data. Idempotent: safe to run on every `make dev`.

    python -m app.cli.seed

Runs only when SEED_DEV_USERS=true (local and CI; never on a server). Creates the three dev
organizations with their batches, and the dev users in Keycloak through the admin API (verified,
enabled, password DEV_USER_PASSWORD, which is reset on every run), then links them to memberships
and batches. Keycloak ids come back from the admin API, so they match the `sub` in real tokens.
Writes as the owner role (bypassing RLS) because seeding is an operator action, not a user action.
"""

import asyncio
import sys
from dataclasses import dataclass, field

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.db.base import new_id
from app.modules.identity.keycloak_admin import KeycloakAdmin, NewUser
from app.modules.identity.models import Batch, BatchMember, Membership, Organization, User


@dataclass(frozen=True)
class SeedOrg:
    slug: str
    name: str
    is_content_publisher: bool = False
    batches: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeedUser:
    username: str  # Keycloak username (= email)
    full_name: str
    roles: tuple[tuple[str, str], ...] = ()  # (org slug, role)
    batches: tuple[tuple[str, str], ...] = ()  # (org slug, batch name)
    realm_roles: tuple[str, ...] = ()


ORGS = (
    SeedOrg("skillifyme", "SkillifyMe", is_content_publisher=True),
    SeedOrg("demo-college", "Demo College", batches=("CSE 2026", "ECE 2026")),
    SeedOrg("other-college", "Other College", batches=("MECH 2026",)),
)

USERS = (
    # The platform admin is a Keycloak realm role; no org membership needed.
    SeedUser("platform.admin@skillifyme.local", "Priya Platform", realm_roles=("platform_admin",)),
    SeedUser("content.admin@skillifyme.local", "Kabir Content", (("skillifyme", "org_admin"),)),
    SeedUser("author@skillifyme.local", "Asha Author", (("skillifyme", "instructor"),)),
    SeedUser("lab.author@skillifyme.local", "Lalit Labs", (("skillifyme", "lab_author"),)),
    # Different roles in different orgs (org switcher).
    SeedUser(
        "multi@skillifyme.local",
        "Meera Multi",
        (("skillifyme", "instructor"), ("demo-college", "instructor")),
    ),
    SeedUser("admin@demo-college.local", "Dev Admin", (("demo-college", "org_admin"),)),
    SeedUser("instructor@demo-college.local", "Ira Instructor", (("demo-college", "instructor"),)),
    SeedUser(
        "cse.student@demo-college.local",
        "Chetan CSE",
        (("demo-college", "student"),),
        batches=(("demo-college", "CSE 2026"),),
    ),
    SeedUser(
        "ece.student@demo-college.local",
        "Esha ECE",
        (("demo-college", "student"),),
        batches=(("demo-college", "ECE 2026"),),
    ),
    SeedUser("admin@other-college.local", "Omar Admin", (("other-college", "org_admin"),)),
    SeedUser(
        "instructor@other-college.local", "Oviya Instructor", (("other-college", "instructor"),)
    ),
    SeedUser(
        "student@other-college.local",
        "Sam Student",
        (("other-college", "student"),),
        batches=(("other-college", "MECH 2026"),),
    ),
)


@dataclass
class SeedResult:
    organizations: dict[str, str] = field(default_factory=dict)  # slug -> id
    users: dict[str, str] = field(default_factory=dict)  # username -> id


async def _upsert_org(session: AsyncSession, org: SeedOrg) -> Organization:
    await session.execute(
        pg_insert(Organization)
        .values(
            id=new_id(),
            name=org.name,
            slug=org.slug,
            is_content_publisher=org.is_content_publisher,
        )
        .on_conflict_do_update(
            constraint="uq_organizations_slug",
            set_={"name": org.name, "is_content_publisher": org.is_content_publisher},
        )
    )
    return (await session.scalars(select(Organization).where(Organization.slug == org.slug))).one()


async def _upsert_batch(session: AsyncSession, org: Organization, name: str) -> Batch:
    existing = await session.scalar(
        select(Batch).where(Batch.organization_id == org.id, func.lower(Batch.name) == name.lower())
    )
    if existing is not None:
        return existing
    batch = Batch(id=new_id(), organization_id=org.id, name=name)
    session.add(batch)
    await session.flush()
    return batch


async def _ensure_keycloak_users(settings: Settings, keycloak: KeycloakAdmin) -> dict[str, str]:
    """Create the dev users that are missing and make every one ready to sign in with
    DEV_USER_PASSWORD. Returns lowercased username -> Keycloak id."""
    if settings.dev_user_password is None:
        msg = "SEED_DEV_USERS is true but DEV_USER_PASSWORD is not set."
        raise SystemExit(msg)
    password = settings.dev_user_password.get_secret_value()
    ids = await keycloak.ensure_users([NewUser(u.username, u.full_name) for u in USERS])
    for spec in USERS:
        keycloak_id = ids[spec.username.lower()]
        await keycloak.prepare_login(keycloak_id, full_name=spec.full_name, password=password)
        for role in spec.realm_roles:
            await keycloak.grant_realm_role(keycloak_id, role)
    return ids


async def _upsert_user(session: AsyncSession, sub: str, email: str, full_name: str) -> User:
    # Dev reconciliation: if this dev user exists under an older Keycloak id (e.g. Keycloak's dev
    # store was recreated), point the row at the current id.
    await session.execute(
        update(User)
        .where(func.lower(User.email) == email.lower(), User.keycloak_sub != sub)
        .values(keycloak_sub=sub)
    )
    await session.execute(
        pg_insert(User)
        .values(id=new_id(), keycloak_sub=sub, email=email, full_name=full_name, status="active")
        .on_conflict_do_update(
            constraint="uq_users_keycloak_sub", set_={"email": email, "full_name": full_name}
        )
    )
    return (await session.scalars(select(User).where(User.keycloak_sub == sub))).one()


async def seed(settings: Settings, keycloak: KeycloakAdmin) -> SeedResult:
    result = SeedResult()
    if not settings.seed_dev_users:
        return result
    keycloak_ids = await _ensure_keycloak_users(settings, keycloak)
    engine = create_async_engine(settings.migration_database_url.get_secret_value())
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session, session.begin():
            await session.execute(text("SELECT 1"))
            orgs: dict[str, Organization] = {}
            batches: dict[tuple[str, str], Batch] = {}
            for spec in ORGS:
                org = await _upsert_org(session, spec)
                orgs[spec.slug] = org
                result.organizations[spec.slug] = str(org.id)
                for name in spec.batches:
                    batches[(spec.slug, name)] = await _upsert_batch(session, org, name)

            for spec_user in USERS:
                user = await _upsert_user(
                    session,
                    keycloak_ids[spec_user.username.lower()],
                    spec_user.username.lower(),
                    spec_user.full_name,
                )
                result.users[spec_user.username] = str(user.id)
                for slug, role in spec_user.roles:
                    await session.execute(
                        pg_insert(Membership)
                        .values(
                            id=new_id(), user_id=user.id, organization_id=orgs[slug].id, role=role
                        )
                        .on_conflict_do_nothing(constraint="uq_memberships_user_org_role")
                    )
                for slug, batch_name in spec_user.batches:
                    batch = batches[(slug, batch_name)]
                    await session.execute(
                        pg_insert(BatchMember)
                        .values(
                            id=new_id(),
                            batch_id=batch.id,
                            organization_id=batch.organization_id,
                            user_id=user.id,
                        )
                        .on_conflict_do_nothing(constraint="uq_batch_members_batch_user")
                    )
    finally:
        await engine.dispose()
    return result


async def _main() -> None:
    settings = get_settings()
    if not settings.seed_dev_users:
        print("Dev seed skipped (SEED_DEV_USERS is not true).")  # noqa: T201
        return
    async with httpx.AsyncClient() as http:
        result = await seed(settings, KeycloakAdmin(http, settings))
    print(  # noqa: T201
        f"Seeded {len(result.organizations)} organizations and {len(result.users)} users."
    )


def main() -> None:
    if get_settings().environment == "production":
        sys.exit("Refusing to seed dev data in production.")
    asyncio.run(_main())


if __name__ == "__main__":
    main()
