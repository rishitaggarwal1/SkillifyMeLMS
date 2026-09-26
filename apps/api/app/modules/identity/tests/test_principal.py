"""Authentication -> principal -> active org -> RLS context, end to end through the API."""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.base import new_id
from app.main import create_app
from app.modules.identity import service
from app.modules.identity.dependencies import TenantSession
from app.modules.identity.models import User
from tests.auth import SigningKey, StaticJwksSource, TokenFactory, make_validator
from tests.factories import Factory
from tests.fixtures import AuthHeaders


@pytest.fixture(scope="module")
async def probe_client(
    settings: Settings, migrated_database: None, signing_key: SigningKey
) -> AsyncIterator[AsyncClient]:
    """An app with one extra route that lists batches through the RLS-scoped session."""
    app: FastAPI = create_app(settings)

    @app.get("/_probe/batches")
    async def batches(session: TenantSession) -> list[str]:
        rows = await session.scalars(text("SELECT id FROM batches ORDER BY id"))
        return [str(r) for r in rows]

    async with LifespanManager(app):
        app.state.jwt_validator = make_validator(settings, StaticJwksSource(signing_key))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


# ---------------------------------------------------------------------------- authentication


async def test_missing_token_is_401_with_challenge(client: AsyncClient) -> None:
    response = await client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_invalid_token_is_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/me", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_first_login_provisions_user(
    client: AsyncClient,
    token_factory: TokenFactory,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    sub = f"kc-{new_id().hex}"
    email = f"{sub}@example.test"
    token = token_factory.token(sub=sub, email=email, name="New Person")

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == email
    assert body["memberships"] == []
    assert body["active_organization_id"] is None
    async with owner_sessionmaker() as s:
        user = (await s.scalars(select(User).where(User.keycloak_sub == sub))).one()
    assert (user.status, user.full_name, str(user.id)) == (
        "active",
        "New Person",
        body["user"]["id"],
    )
    assert user.last_login_at is not None


async def test_unverified_email_is_rejected(
    client: AsyncClient, token_factory: TokenFactory
) -> None:
    token = token_factory.token(sub=f"kc-{new_id().hex}", email_verified=False)
    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert (response.status_code, response.json()["error"]["code"]) == (403, "email_not_verified")


async def test_email_owned_by_another_account_is_409(
    client: AsyncClient, factory: Factory, token_factory: TokenFactory
) -> None:
    existing = await factory.user()
    token = token_factory.token(sub=f"kc-{new_id().hex}", email=existing.email)
    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert (response.status_code, response.json()["error"]["code"]) == (409, "email_in_use")


async def test_disabled_user_is_rejected(
    client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    user = await factory.user(status="disabled")
    response = await client.get("/api/v1/me", headers=auth_headers(user))
    assert (response.status_code, response.json()["error"]["code"]) == (403, "account_disabled")


# ---------------------------------------------------------------------------- active organization


async def test_single_membership_is_the_default_org(
    client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    org = await factory.org(name="Solo College")
    user = await factory.member(org, "org_admin")

    body = (await client.get("/api/v1/me", headers=auth_headers(user))).json()

    assert body["active_organization_id"] == str(org.id)
    assert body["active_roles"] == ["org_admin"]
    assert "batch.manage" in body["permissions"]
    assert body["memberships"] == [
        {
            "organization": {
                "id": str(org.id),
                "name": "Solo College",
                "slug": org.slug,
                "is_content_publisher": False,
            },
            "roles": ["org_admin"],
        }
    ]


async def test_roles_follow_the_selected_org(
    client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    org_a, org_b = await factory.org(), await factory.org(publisher=True)
    user = await factory.member(org_a, "instructor")
    await factory.member(org_b, "student", "lab_author", user=user)

    no_org = (await client.get("/api/v1/me", headers=auth_headers(user))).json()
    in_a = (await client.get("/api/v1/me", headers=auth_headers(user, org=org_a.id))).json()
    in_b = (await client.get("/api/v1/me", headers=auth_headers(user, org=org_b.id))).json()

    assert no_org["active_organization_id"] is None  # two orgs: the client must choose
    assert len(no_org["memberships"]) == 2
    assert in_a["active_roles"] == ["instructor"]
    assert "member.read" in in_a["permissions"]
    assert in_b["active_roles"] == ["lab_author", "student"]
    assert "member.read" not in in_b["permissions"]


async def test_selecting_a_foreign_org_is_403(
    client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    mine, theirs = await factory.org(), await factory.org()
    user = await factory.member(mine, "org_admin")
    response = await client.get("/api/v1/me", headers=auth_headers(user, org=theirs.id))
    assert (response.status_code, response.json()["error"]["code"]) == (
        403,
        "organization_access_denied",
    )


async def test_malformed_org_header_is_422(
    client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    user = await factory.user()
    headers = {**auth_headers(user), "X-Organization-Id": "not-a-uuid"}
    response = await client.get("/api/v1/me", headers=headers)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error")


async def test_platform_admin_may_act_in_any_existing_org(
    client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    org = await factory.org()
    admin = await factory.user()

    body = (
        await client.get("/api/v1/me", headers=auth_headers(admin, org=org.id, platform_admin=True))
    ).json()
    missing = await client.get(
        "/api/v1/me", headers=auth_headers(admin, org=new_id(), platform_admin=True)
    )

    assert body["is_platform_admin"] is True
    assert body["active_organization_id"] == str(org.id)
    assert body["active_roles"] == []
    assert "org.manage" in body["permissions"]
    assert missing.status_code == 403


async def test_archived_org_memberships_are_ignored(
    client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    active, archived = await factory.org(), await factory.org(status="archived")
    user = await factory.member(active, "student")
    await factory.member(archived, "org_admin", user=user)

    body = (await client.get("/api/v1/me", headers=auth_headers(user))).json()
    denied = await client.get("/api/v1/me", headers=auth_headers(user, org=archived.id))

    assert [m["organization"]["id"] for m in body["memberships"]] == [str(active.id)]
    assert denied.status_code == 403


# ---------------------------------------------------------------------------- RLS context


async def test_request_session_is_scoped_to_principal_and_org(
    probe_client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    org_a, org_b = await factory.org(), await factory.org()
    a1, a2, b1 = await factory.batch(org_a), await factory.batch(org_a), await factory.batch(org_b)
    admin = await factory.member(org_a, "org_admin")
    await factory.member(org_b, "student", user=admin)
    await factory.add_to_batch(b1, admin)

    as_admin_in_a = (
        await probe_client.get("/_probe/batches", headers=auth_headers(admin, org=org_a.id))
    ).json()
    as_student_in_b = (
        await probe_client.get("/_probe/batches", headers=auth_headers(admin, org=org_b.id))
    ).json()

    assert set(as_admin_in_a) == {str(a1.id), str(a2.id)}
    assert as_student_in_b == [str(b1.id)]


# ---------------------------------------------------------------------------- caching


async def test_principal_is_cached_until_invalidated(
    app: FastAPI, client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    org_a, org_b = await factory.org(), await factory.org()
    user = await factory.member(org_a, "student")
    first = (await client.get("/api/v1/me", headers=auth_headers(user))).json()

    await factory.member(org_b, "student", user=user)  # changed behind the cache's back
    cached = (await client.get("/api/v1/me", headers=auth_headers(user))).json()
    await service.invalidate_principal(app.state.redis, user.keycloak_sub)
    fresh = (await client.get("/api/v1/me", headers=auth_headers(user))).json()

    assert len(first["memberships"]) == 1
    assert len(cached["memberships"]) == 1
    assert {UUID(m["organization"]["id"]) for m in fresh["memberships"]} == {org_a.id, org_b.id}
