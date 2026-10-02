"""Platform-admin endpoints (Phase 2.5): users across orgs, disable/enable, inviting an org's
admin, the cross-org course list, the platform audit log and the dashboard counts. Role checks
for every route are in tests/test_endpoint_roles.py; these test behaviour."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from app.modules.enrollments.models import Enrollment
from app.modules.enrollments.service import start_of_day_ist
from app.modules.identity.models import User
from tests.factories import Factory
from tests.fakes import FakeKeycloakAdmin

Headers = Callable[..., dict[str, str]]


async def _platform(factory: Factory, auth_headers: Headers) -> tuple[User, dict[str, str]]:
    admin = await factory.user()
    return admin, auth_headers(admin, platform_admin=True)


# ---------------------------------------------------------------------------- users


async def test_users_across_orgs_with_filters(
    client: AsyncClient, factory: Factory, auth_headers: Headers
) -> None:
    tag = uuid7().hex[-8:]
    college_a, college_b = await factory.org(name=f"A {tag}"), await factory.org(name=f"B {tag}")
    asha = await factory.user(email=f"asha.{tag}@college.test", full_name=f"Asha Rao {tag}")
    await factory.member(college_a, "student", user=asha)
    await factory.member(college_b, "instructor", "org_admin", user=asha)
    ravi_user = await factory.user(
        email=f"ravi.{tag}@college.test", full_name=f"Ravi Iyer {tag}", status="disabled"
    )
    ravi = await factory.member(college_b, "student", user=ravi_user)
    batch = await factory.batch(college_b)
    await factory.add_to_batch(batch, asha)
    _, h = await _platform(factory, auth_headers)

    async def ids(**params: Any) -> list[str]:
        response = await client.get("/api/v1/platform/users", headers=h, params=params)
        assert response.status_code == 200, response.text
        return [u["id"] for u in response.json()["items"]]

    assert set(await ids(q=tag)) == {str(asha.id), str(ravi.id)}  # name or email, both orgs
    assert await ids(q=f"asha.{tag}") == [str(asha.id)]
    assert await ids(q=tag, role="org_admin") == [str(asha.id)]
    assert set(await ids(organization_id=str(college_b.id))) == {str(asha.id), str(ravi.id)}
    assert await ids(organization_id=str(college_a.id)) == [str(asha.id)]
    assert await ids(q=tag, status="disabled") == [str(ravi.id)]
    assert await ids(q=f"%{tag}") == []  # % is matched literally, not as a wildcard

    listed = await client.get("/api/v1/platform/users", headers=h, params={"q": f"asha.{tag}"})
    [row] = listed.json()["items"]
    assert [(m["organization"]["name"], m["roles"]) for m in row["memberships"]] == [
        (f"A {tag}", ["student"]),
        (f"B {tag}", ["instructor", "org_admin"]),
    ]
    detail = (await client.get(f"/api/v1/platform/users/{asha.id}", headers=h)).json()
    assert detail["batches"] == [
        {"id": str(batch.id), "name": batch.name, "organization_id": str(college_b.id)}
    ]
    missing = await client.get(f"/api/v1/platform/users/{uuid7()}", headers=h)
    assert missing.status_code == 404


async def test_disable_blocks_sign_in_and_api_then_enable_restores(
    client: AsyncClient, factory: Factory, auth_headers: Headers, fake_idp: FakeKeycloakAdmin
) -> None:
    college = await factory.org()
    student = await factory.member(college, "student")
    _, h = await _platform(factory, auth_headers)
    me = auth_headers(student, org=college.id)
    assert (await client.get("/api/v1/me", headers=me)).status_code == 200  # cached principal

    disabled = await client.post(f"/api/v1/platform/users/{student.id}/disable", headers=h)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "disabled"
    assert student.keycloak_sub in fake_idp.disabled  # Keycloak: no sign-in, sessions ended
    blocked = await client.get("/api/v1/me", headers=me)  # the cache was invalidated
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "account_disabled"

    again = await client.post(f"/api/v1/platform/users/{student.id}/disable", headers=h)
    assert again.status_code == 200  # idempotent

    enabled = await client.post(f"/api/v1/platform/users/{student.id}/enable", headers=h)
    assert enabled.json()["status"] == "active"
    assert student.keycloak_sub not in fake_idp.disabled
    assert (await client.get("/api/v1/me", headers=me)).status_code == 200

    audit = await client.get(
        "/api/v1/platform/audit-log", headers=h, params={"target_id": str(student.id)}
    )
    entries = audit.json()["items"]
    assert [e["action"] for e in entries] == ["user.enabled", "user.disabled"]  # one each
    assert all(e["organization_id"] is None for e in entries)  # platform-level actions
    assert entries[1]["before"] == {"status": "active"}
    assert entries[1]["after"] == {"status": "disabled"}


async def test_enable_returns_a_never_signed_in_user_to_invited(
    client: AsyncClient, factory: Factory, auth_headers: Headers
) -> None:
    college = await factory.org()
    invited = await factory.user(status="invited")
    await factory.member(college, "student", user=invited)
    await factory.invitation(college, user=invited)
    _, h = await _platform(factory, auth_headers)
    await client.post(f"/api/v1/platform/users/{invited.id}/disable", headers=h)
    enabled = await client.post(f"/api/v1/platform/users/{invited.id}/enable", headers=h)
    assert enabled.json()["status"] == "invited"  # their setup link still applies


async def test_cannot_disable_self_and_keycloak_failure_changes_nothing(
    client: AsyncClient, factory: Factory, auth_headers: Headers, fake_idp: FakeKeycloakAdmin
) -> None:
    admin, h = await _platform(factory, auth_headers)
    self_disable = await client.post(f"/api/v1/platform/users/{admin.id}/disable", headers=h)
    assert self_disable.status_code == 409
    assert self_disable.json()["error"]["code"] == "cannot_disable_self"

    student = await factory.member(await factory.org(), "student")
    fake_idp.fail_admin = True
    failed = await client.post(f"/api/v1/platform/users/{student.id}/disable", headers=h)
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "identity_provider_unavailable"
    fake_idp.fail_admin = False
    detail = await client.get(f"/api/v1/platform/users/{student.id}", headers=h)
    assert detail.json()["status"] == "active"  # rolled back with the request
    audit = await client.get(
        "/api/v1/platform/audit-log", headers=h, params={"target_id": str(student.id)}
    )
    assert audit.json()["items"] == []


# ---------------------------------------------------------------------------- org admins


async def test_invite_an_organizations_first_admin(
    client: AsyncClient,
    factory: Factory,
    auth_headers: Headers,
    fake_idp: FakeKeycloakAdmin,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _, h = await _platform(factory, auth_headers)
    created = await client.post(
        "/api/v1/organizations",
        headers=h,
        json={"name": "Shivaji College", "slug": f"shivaji-{uuid7().hex[-8:]}"},
    )
    org_id = created.json()["id"]
    email = f"principal.{uuid7().hex[-6:]}@shivaji.test"

    invited = await client.post(
        f"/api/v1/platform/organizations/{org_id}/admins",
        headers=h,
        json={"email": email.upper(), "full_name": "Meera Kulkarni"},
    )
    assert invited.status_code == 201, invited.text
    body = invited.json()
    assert (body["email"], body["roles"], body["status"]) == (email, ["org_admin"], "pending")
    assert fake_idp.setup_emails == [fake_idp.users[email]]  # new account: setup email sent

    async with owner_sessionmaker() as s:
        roles = (
            await s.scalars(
                text(
                    "SELECT m.role FROM memberships m JOIN users u ON u.id = m.user_id "
                    "WHERE m.organization_id = :org AND u.email = :email"
                ),
                {"org": org_id, "email": email},
            )
        ).all()
        audit_org = await s.scalar(
            text("SELECT organization_id FROM audit_log WHERE target_id = :id"),
            {"id": body["id"]},
        )
    assert list(roles) == ["org_admin"]
    assert str(audit_org) == org_id  # recorded in the new org's audit log

    duplicate = await client.post(
        f"/api/v1/platform/organizations/{org_id}/admins", headers=h, json={"email": email}
    )
    assert duplicate.status_code == 409
    unknown = await client.post(
        f"/api/v1/platform/organizations/{uuid7()}/admins", headers=h, json={"email": email}
    )
    assert unknown.status_code == 404
    archived = await factory.org(status="archived")
    refused = await client.post(
        f"/api/v1/platform/organizations/{archived.id}/admins", headers=h, json={"email": email}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "organization_archived"


async def test_organization_search(
    client: AsyncClient, factory: Factory, auth_headers: Headers
) -> None:
    tag = uuid7().hex[-8:]
    match = await factory.org(name=f"Ramanujan Institute {tag}")
    await factory.org(name=f"Other {uuid7().hex[-8:]}")
    _, h = await _platform(factory, auth_headers)
    found = await client.get(
        "/api/v1/organizations", headers=h, params={"q": f"ramanujan institute {tag}"}
    )
    assert [o["id"] for o in found.json()["items"]] == [str(match.id)]


# ---------------------------------------------------------------------------- courses


async def test_courses_across_orgs(
    client: AsyncClient, factory: Factory, auth_headers: Headers
) -> None:
    publisher = await factory.org(name=f"Publisher {uuid7().hex[-6:]}", publisher=True)
    college = await factory.org()
    published = await factory.course(publisher, title="Python Foundations")
    module = await factory.module(published)
    lesson = await factory.lesson(module, lesson_type="notes")
    await factory.version(published, lesson)
    grant = await factory.assignment(published, college)
    await factory.assignment(published, college, batch=await factory.batch(college), parent=grant)
    await factory.assignment(published, publisher, batch=await factory.batch(publisher))
    draft = await factory.course(publisher, title="Draft course")
    _, h = await _platform(factory, auth_headers)

    response = await client.get(
        "/api/v1/platform/courses", headers=h, params={"organization_id": str(publisher.id)}
    )
    assert response.status_code == 200, response.text
    rows = {c["id"]: c for c in response.json()["items"]}
    assert set(rows) == {str(published.id), str(draft.id)}
    row = rows[str(published.id)]
    assert row["owner"] == {"id": str(publisher.id), "name": publisher.name}
    assert row["current_version"]["version"] == "1.0"
    assert (row["org_grant_count"], row["batch_assignment_count"]) == (1, 2)
    assert rows[str(draft.id)]["current_version"] is None
    assert (
        rows[str(draft.id)]["org_grant_count"],
        rows[str(draft.id)]["batch_assignment_count"],
    ) == (0, 0)

    archived = await client.get(
        "/api/v1/platform/courses",
        headers=h,
        params={"organization_id": str(publisher.id), "status": "archived"},
    )
    assert archived.json()["items"] == []


# ---------------------------------------------------------------------------- audit log


async def test_platform_audit_log_filters(
    client: AsyncClient, factory: Factory, auth_headers: Headers
) -> None:
    college, other = await factory.org(), await factory.org()
    actor = await factory.member(college, "org_admin")
    action = f"test.{uuid7().hex[-8:]}"
    in_college = await factory.audit(college, actor, action)
    in_other = await factory.audit(other, actor, action)
    platform_level = await factory.audit(None, actor, action)
    admin, h = await _platform(factory, auth_headers)
    # Whatever org is active, the platform log covers every org.
    h_in_org = auth_headers(admin, org=college.id, platform_admin=True)

    async def ids(headers: dict[str, str], **params: Any) -> set[str]:
        response = await client.get(
            "/api/v1/platform/audit-log", headers=headers, params={"action": action, **params}
        )
        assert response.status_code == 200, response.text
        return {e["id"] for e in response.json()["items"]}

    everything = {str(in_college), str(in_other), str(platform_level)}
    assert await ids(h) == everything
    assert await ids(h_in_org) == everything
    assert await ids(h, organization_id=str(other.id)) == {str(in_other)}
    assert await ids(h, actor_user_id=str(actor.id)) == everything
    now = datetime.now(UTC)
    assert await ids(h, since=(now - timedelta(minutes=5)).isoformat()) == everything
    assert await ids(h, until=(now - timedelta(minutes=5)).isoformat()) == set()
    assert await ids(h, since=(now + timedelta(minutes=5)).isoformat()) == set()
    naive = await client.get(
        "/api/v1/platform/audit-log", headers=h, params={"since": "2026-01-01T00:00:00"}
    )
    assert naive.status_code == 422  # a time zone is required


# ---------------------------------------------------------------------------- summary


def test_start_of_day_is_midnight_in_india() -> None:
    # 20:00 UTC on 1 Oct is 01:30 IST on 2 Oct, so "today" began at 18:30 UTC on 1 Oct.
    assert start_of_day_ist(datetime(2026, 10, 1, 20, 0, tzinfo=UTC)) == datetime(
        2026, 10, 1, 18, 30, tzinfo=UTC
    )
    # 10:00 UTC is 15:30 IST the same day.
    assert start_of_day_ist(datetime(2026, 10, 1, 10, 0, tzinfo=UTC)) == datetime(
        2026, 9, 30, 18, 30, tzinfo=UTC
    )


async def test_summary_counts(
    client: AsyncClient,
    factory: Factory,
    auth_headers: Headers,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _, h = await _platform(factory, auth_headers)
    before = (await client.get("/api/v1/platform/summary", headers=h)).json()

    college = await factory.org()
    await factory.org(status="archived")
    student, idle = (
        await factory.member(college, "student"),
        await factory.member(college, "student"),
    )
    await factory.member(college, "instructor")
    course = await factory.course(college)
    lesson = await factory.lesson(await factory.module(course), lesson_type="notes")
    await factory.version(course, lesson)
    await factory.course(college)  # a draft: active, not published
    active = await factory.enrollment(course, student, college)
    await factory.enrollment(course, idle, college)
    async with owner_sessionmaker() as s, s.begin():
        await s.execute(
            update(Enrollment)
            .where(Enrollment.id == active.id)
            .values(last_accessed_at=datetime.now(UTC))
        )

    after = (await client.get("/api/v1/platform/summary", headers=h)).json()

    def delta(*path: str) -> int:
        a: Any = after
        b: Any = before
        for key in path:
            a, b = a.get(key, 0), b.get(key, 0)
        return int(a) - int(b)

    assert delta("organizations", "active") == 1
    assert delta("organizations", "archived") == 1
    assert delta("users", "active") == 3
    assert delta("users_by_role", "student") == 2
    assert delta("users_by_role", "instructor") == 1
    assert delta("courses", "active") == 2
    assert delta("courses", "published") == 1
    assert delta("enrollments", "active") == 2
    assert delta("active_today") == 1
    assert after["active_since"].startswith(start_of_day_ist(datetime.now(UTC)).date().isoformat())
