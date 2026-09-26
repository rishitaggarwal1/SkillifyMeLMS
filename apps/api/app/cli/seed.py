"""Seed local/dev data. Idempotent: safe to run on every `make dev`.

    python -m app.cli.seed

Creates the three dev organizations with their batches, and links the dev-realm Keycloak users
(infra/local/keycloak/realm/skillifyme-realm.json) to memberships and batches. Keycloak user IDs are
looked up through the admin API, so they match the `sub` in real tokens. Writes as the owner role
(bypassing RLS) because seeding is an operator action, not a user action.
"""

import asyncio
import sys
from dataclasses import dataclass, field

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.db.base import new_id
from app.modules.identity.keycloak_admin import KeycloakAdmin
from app.modules.identity.models import Batch, BatchMember, Membership, Organization, User


@dataclass(frozen=True)
class SeedOrg:
    slug: str
    name: str
    is_content_publisher: bool = False
    batches: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeedUser:
    username: str  # Keycloak username (= email in the dev realm)
    roles: tuple[tuple[str, str], ...] = ()  # (org slug, role)
    batches: tuple[tuple[str, str], ...] = ()  # (org slug, batch name)


ORGS = (
    SeedOrg("skillifyme", "SkillifyMe", is_content_publisher=True),
    SeedOrg("demo-college", "Demo College", batches=("CSE 2026", "ECE 2026")),
    SeedOrg("other-college", "Other College", batches=("MECH 2026",)),
)

USERS = (
    # Platform admin comes from the Keycloak realm role; no org membership needed.
    SeedUser("platform.admin@skillifyme.local"),
    SeedUser("content.admin@skillifyme.local", roles=(("skillifyme", "org_admin"),)),
    SeedUser("author@skillifyme.local", roles=(("skillifyme", "instructor"),)),
    SeedUser("lab.author@skillifyme.local", roles=(("skillifyme", "lab_author"),)),
    # Different roles in different orgs (org switcher).
    SeedUser(
        "multi@skillifyme.local",
        roles=(("skillifyme", "instructor"), ("demo-college", "instructor")),
    ),
    SeedUser("admin@demo-college.local", roles=(("demo-college", "org_admin"),)),
    SeedUser("instructor@demo-college.local", roles=(("demo-college", "instructor"),)),
    SeedUser(
        "cse.student@demo-college.local",
        roles=(("demo-college", "student"),),
        batches=(("demo-college", "CSE 2026"),),
    ),
    SeedUser(
        "ece.student@demo-college.local",
        roles=(("demo-college", "student"),),
        batches=(("demo-college", "ECE 2026"),),
    ),
    SeedUser("admin@other-college.local", roles=(("other-college", "org_admin"),)),
    SeedUser("instructor@other-college.local", roles=(("other-college", "instructor"),)),
    SeedUser(
        "student@other-college.local",
        roles=(("other-college", "student"),),
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


async def _upsert_user(session: AsyncSession, kc_user: dict[str, object]) -> User:
    sub, email = str(kc_user["id"]), str(kc_user["email"])
    full_name = " ".join(str(kc_user.get(k) or "") for k in ("firstName", "lastName")).strip()
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
                kc_user = await keycloak.find_user_by_username(spec_user.username)
                if kc_user is None:
                    msg = (
                        f"Keycloak user {spec_user.username!r} not found; "
                        "is the dev realm imported?"
                    )
                    raise SystemExit(msg)
                user = await _upsert_user(session, kc_user)
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
