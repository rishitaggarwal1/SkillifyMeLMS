"""Members (search, roles, removal) and invitations (invite, revoke, resend, accept, expire)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from app.db.tenancy import set_tenant_context
from app.modules.identity import service
from app.modules.identity.tests.conftest import OrgSetup
from tests.auth import TokenFactory
from tests.factories import Factory
from tests.fakes import FakeKeycloakAdmin


async def _scalar(owner: async_sessionmaker[AsyncSession], sql: str, **params: Any) -> Any:
    async with owner() as s:
        return await s.scalar(text(sql), params)


# ---------------------------------------------------------------------------- members


async def test_member_search_and_filters(
    client: AsyncClient, org_setup: OrgSetup, factory: Factory
) -> None:
    tag = uuid7().hex[-6:]
    priya = await factory.member(org_setup.org, "student", user=await factory.user(
        email=f"priya.{tag}@college.test", full_name="Priya Sharma"))  # fmt: skip
    await factory.member(org_setup.org, "student", user=await factory.user(
        email=f"rahul.{tag}@college.test", full_name="Rahul Verma"))  # fmt: skip
    await factory.member(await factory.org(), "student", user=await factory.user(
        email=f"priya.other.{tag}@elsewhere.test", full_name="Priya Other"))  # fmt: skip
    h = org_setup.h(org_setup.instructor)

    by_name = await client.get("/api/v1/members", headers=h, params={"q": "PRIYA"})
    by_email = await client.get("/api/v1/members", headers=h, params={"q": f"rahul.{tag}"})
    instructors = await client.get("/api/v1/members", headers=h, params={"role": "instructor"})
    in_batch = await client.get(
        "/api/v1/members", headers=h, params={"batch_id": str(org_setup.batch.id)}
    )
    like_chars = await client.get("/api/v1/members", headers=h, params={"q": "%"})

    assert [m["user"]["id"] for m in by_name.json()["items"]] == [str(priya.id)]  # not other org
    assert [m["user"]["full_name"] for m in by_email.json()["items"]] == ["Rahul Verma"]
    assert [m["user"]["id"] for m in instructors.json()["items"]] == [str(org_setup.instructor.id)]
    [member] = in_batch.json()["items"]
    assert member["user"]["id"] == str(org_setup.student.id)
    assert member["batch_ids"] == [str(org_setup.batch.id)]
    assert like_chars.json()["items"] == []  # % is matched literally


async def test_member_list_is_cursor_paginated(
    client: AsyncClient, org_setup: OrgSetup, factory: Factory
) -> None:
    for _ in range(3):
        await factory.member(org_setup.org, "student")
    h = org_setup.h(org_setup.admin)
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get("/api/v1/members", headers=h, params=params)).json()
        seen += [m["user"]["id"] for m in page["items"]]
        if not (cursor := page["next_cursor"]):
            break
    assert len(seen) == len(set(seen)) == 7  # 4 role users + 3 students


async def test_change_roles_and_last_admin_guard(
    app: FastAPI, client: AsyncClient, org_setup: OrgSetup, factory: Factory
) -> None:
    h = org_setup.h(org_setup.admin)
    promoted = await client.patch(
        f"/api/v1/members/{org_setup.student.id}",
        headers=h,
        json={"roles": ["instructor", "student"]},
    )
    demote_self = await client.patch(
        f"/api/v1/members/{org_setup.admin.id}", headers=h, json={"roles": ["instructor"]}
    )
    remove_self = await client.delete(f"/api/v1/members/{org_setup.admin.id}", headers=h)
    duplicate_roles = await client.patch(
        f"/api/v1/members/{org_setup.student.id}", headers=h, json={"roles": ["student", "student"]}
    )
    # The student's cached principal was invalidated: they now have instructor permissions.
    me = await client.get("/api/v1/me", headers=org_setup.h(org_setup.student))

    assert promoted.json()["roles"] == ["instructor", "student"]
    assert (demote_self.status_code, demote_self.json()["error"]["code"]) == (409, "last_org_admin")
    assert remove_self.status_code == 409
    assert duplicate_roles.status_code == 422
    assert "member.read" in me.json()["permissions"]


async def test_remove_member_drops_batches_with_events(
    client: AsyncClient, org_setup: OrgSetup, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    response = await client.delete(
        f"/api/v1/members/{org_setup.student.id}", headers=org_setup.h(org_setup.admin)
    )
    gone = await client.get(
        f"/api/v1/members/{org_setup.student.id}", headers=org_setup.h(org_setup.admin)
    )
    kicked = await client.get("/api/v1/me", headers=org_setup.h(org_setup.student))

    assert response.status_code == 204
    assert gone.status_code == 404
    assert kicked.status_code == 403  # no longer a member of that org
    reason = await _scalar(
        owner_sessionmaker,
        "SELECT payload->>'reason' FROM outbox_events WHERE event_type = 'batch_member_removed' "
        "AND payload->>'user_id' = :u",
        u=str(org_setup.student.id),
    )
    assert reason == "left_organization"


# ---------------------------------------------------------------------------- invitations


async def test_invite_new_person(
    client: AsyncClient,
    org_setup: OrgSetup,
    fake_idp: FakeKeycloakAdmin,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    email = f"New.Person.{uuid7().hex[-6:]}@College.test"
    response = await client.post(
        "/api/v1/invitations",
        headers=org_setup.h(org_setup.admin),
        json={
            "email": email,
            "full_name": "New Person",
            "roles": ["student"],
            "batch_ids": [str(org_setup.batch.id)],
        },
    )
    body = response.json()
    again = await client.post(
        "/api/v1/invitations",
        headers=org_setup.h(org_setup.admin),
        json={"email": email, "roles": ["student"]},
    )
    member = await client.get(
        f"/api/v1/members/{body['user_id']}", headers=org_setup.h(org_setup.admin)
    )

    assert response.status_code == 201
    assert body["email"] == email.lower()
    assert body["status"] == "pending"
    assert fake_idp.setup_emails == [fake_idp.users[email.lower()]]  # password-setup email
    assert (again.status_code, again.json()["error"]["code"]) == (409, "invitation_pending")
    assert member.json()["roles"] == ["student"]
    assert member.json()["user"]["status"] == "invited"
    assert member.json()["batch_ids"] == [str(org_setup.batch.id)]
    reason = await _scalar(
        owner_sessionmaker,
        "SELECT payload->>'reason' FROM outbox_events WHERE payload->>'user_id' = :u",
        u=body["user_id"],
    )
    assert reason == "invitation"


async def test_invite_existing_account_from_another_org(
    client: AsyncClient, org_setup: OrgSetup, factory: Factory, fake_idp: FakeKeycloakAdmin
) -> None:
    elsewhere = await factory.member(await factory.org(), "instructor")
    fake_idp.add_existing(elsewhere.email, elsewhere.keycloak_sub)

    response = await client.post(
        "/api/v1/invitations",
        headers=org_setup.h(org_setup.admin),
        json={"email": elsewhere.email, "roles": ["instructor"]},
    )
    already = await client.post(
        "/api/v1/invitations",
        headers=org_setup.h(org_setup.admin),
        json={"email": org_setup.student.email, "roles": ["student"]},
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == str(elsewhere.id)  # same account, new membership
    assert fake_idp.setup_emails == []  # already has a password
    fake_idp.add_existing(org_setup.student.email, org_setup.student.keycloak_sub)
    assert already.status_code == 409


async def test_invite_rejects_foreign_batches(
    client: AsyncClient, org_setup: OrgSetup, factory: Factory, fake_idp: FakeKeycloakAdmin
) -> None:
    foreign = await factory.batch(await factory.org())
    response = await client.post(
        "/api/v1/invitations",
        headers=org_setup.h(org_setup.admin),
        json={"email": "x@college.test", "roles": ["student"], "batch_ids": [str(foreign.id)]},
    )
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid_batches")
    assert fake_idp.users == {}  # nothing created in Keycloak


async def test_revoke_and_resend(
    client: AsyncClient,
    org_setup: OrgSetup,
    fake_idp: FakeKeycloakAdmin,
) -> None:
    h = org_setup.h(org_setup.admin)
    invite = (
        await client.post(
            "/api/v1/invitations",
            headers=h,
            json={"email": f"r{uuid7().hex[-6:]}@college.test", "roles": ["student"],
                  "batch_ids": [str(org_setup.batch.id)]},
        )
    ).json()  # fmt: skip
    resent = await client.post(f"/api/v1/invitations/{invite['id']}/resend", headers=h)
    revoked = await client.delete(f"/api/v1/invitations/{invite['id']}", headers=h)
    revoke_again = await client.delete(f"/api/v1/invitations/{invite['id']}", headers=h)
    member = await client.get(f"/api/v1/members/{invite['user_id']}", headers=h)

    assert resent.status_code == 200
    assert len(fake_idp.setup_emails) == 2
    assert revoked.json()["status"] == "revoked"
    assert revoke_again.status_code == 409
    assert member.status_code == 404  # access granted by the invitation was withdrawn


async def test_first_login_accepts_invitation(
    client: AsyncClient,
    org_setup: OrgSetup,
    fake_idp: FakeKeycloakAdmin,
    token_factory: TokenFactory,
) -> None:
    email = f"joiner.{uuid7().hex[-6:]}@college.test"
    invite = (
        await client.post(
            "/api/v1/invitations",
            headers=org_setup.h(org_setup.admin),
            json={"email": email, "roles": ["student"]},
        )
    ).json()
    token = token_factory.token(sub=fake_idp.users[email], email=email, name="Joiner")

    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    invitations = await client.get(
        "/api/v1/invitations", headers=org_setup.h(org_setup.admin), params={"status": "accepted"}
    )

    assert me.json()["active_organization_id"] == str(org_setup.org.id)
    assert invite["id"] in {i["id"] for i in invitations.json()["items"]}


async def test_expired_invitations_withdraw_access(
    app: FastAPI,
    client: AsyncClient,
    org_setup: OrgSetup,
    fake_idp: FakeKeycloakAdmin,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    invite = (
        await client.post(
            "/api/v1/invitations",
            headers=org_setup.h(org_setup.admin),
            json={"email": f"late.{uuid7().hex[-6:]}@college.test", "roles": ["student"],
                  "batch_ids": [str(org_setup.batch.id)]},
        )
    ).json()  # fmt: skip

    async with app.state.sessionmaker() as s, s.begin():
        await set_tenant_context(s, organization_id=None, user_id=None, is_platform_admin=True)
        expired = await service.expire_invitations(s, datetime.now(UTC) + timedelta(days=30))

    status = await _scalar(
        owner_sessionmaker, "SELECT status FROM invitations WHERE id = :i", i=UUID(invite["id"])
    )
    memberships = await _scalar(
        owner_sessionmaker,
        "SELECT count(*) FROM memberships WHERE user_id = :u",
        u=UUID(invite["user_id"]),
    )
    assert expired >= 1
    assert status == "expired"
    assert memberships == 0
