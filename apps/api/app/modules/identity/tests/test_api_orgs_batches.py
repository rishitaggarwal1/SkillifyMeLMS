"""Organizations (platform admin) and batches (org admin) APIs, including audit rows and the batch
membership events Phase 2 consumes."""

from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils.compat import uuid7

from app.modules.identity.tests.conftest import OrgSetup
from tests.factories import Factory
from tests.fixtures import AuthHeaders


async def _audit(owner: async_sessionmaker[AsyncSession], target_id: object) -> list[Any]:
    async with owner() as s:
        rows = await s.execute(
            text(
                "SELECT action, actor_user_id, organization_id, before, after, ip "
                "FROM audit_log WHERE target_id = :t ORDER BY id"
            ),
            {"t": str(target_id)},
        )
        return list(rows)


async def _events(owner: async_sessionmaker[AsyncSession], batch_id: UUID) -> list[Any]:
    async with owner() as s:
        rows = await s.execute(
            text(
                "SELECT event_type, payload, organization_id, headers FROM outbox_events "
                "WHERE aggregate_type = 'batch_member' AND aggregate_id = :b ORDER BY occurred_at"
            ),
            {"b": batch_id},
        )
        return list(rows)


# ---------------------------------------------------------------------------- organizations


async def test_platform_admin_creates_updates_and_archives_org(
    client: AsyncClient,
    factory: Factory,
    auth_headers: AuthHeaders,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await factory.user()
    h = auth_headers(admin, platform_admin=True)
    slug = f"college-{uuid7().hex[-8:]}"

    created = await client.post(
        "/api/v1/organizations",
        headers=h,
        json={"name": "  New College ", "slug": slug.upper(), "is_content_publisher": False},
    )
    org_id = created.json()["id"]
    duplicate = await client.post(
        "/api/v1/organizations", headers=h, json={"name": "Dup", "slug": slug}
    )
    updated = await client.patch(
        f"/api/v1/organizations/{org_id}", headers=h, json={"is_content_publisher": True}
    )
    archived = await client.delete(f"/api/v1/organizations/{org_id}", headers=h)
    listed = await client.get("/api/v1/organizations?status=archived&limit=100", headers=h)

    assert created.status_code == 201
    assert created.json()["name"] == "New College"
    assert created.json()["slug"] == slug  # normalized to lower case
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "slug_taken"
    assert updated.json()["is_content_publisher"] is True
    assert archived.json()["status"] == "archived"
    assert org_id in {o["id"] for o in listed.json()["items"]}
    actions = [r.action for r in await _audit(owner_sessionmaker, org_id)]
    assert actions == ["organization.created", "organization.updated", "organization.archived"]


async def test_org_validation(
    client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    h = auth_headers(await factory.user(), platform_admin=True)
    bad_slug = await client.post(
        "/api/v1/organizations", headers=h, json={"name": "X", "slug": "a b"}
    )
    no_name = await client.post(
        "/api/v1/organizations", headers=h, json={"name": " ", "slug": "ok-slug"}
    )
    assert (bad_slug.status_code, no_name.status_code) == (422, 422)


async def test_current_org_for_members(client: AsyncClient, org_setup: OrgSetup) -> None:
    response = await client.get(
        "/api/v1/organizations/current", headers=org_setup.h(org_setup.student)
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(org_setup.org.id)


# ---------------------------------------------------------------------------- batches


async def test_batch_lifecycle(
    client: AsyncClient, org_setup: OrgSetup, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    h = org_setup.h(org_setup.admin)
    created = await client.post(
        "/api/v1/batches", headers=h, json={"name": " CSE 2027 ", "description": "Second years"}
    )
    batch_id = created.json()["id"]
    duplicate = await client.post("/api/v1/batches", headers=h, json={"name": "cse 2027"})
    renamed = await client.patch(
        f"/api/v1/batches/{batch_id}", headers=h, json={"name": "CSE 2027 A"}
    )
    listed = await client.get("/api/v1/batches", headers=org_setup.h(org_setup.instructor))
    archived = await client.delete(f"/api/v1/batches/{batch_id}", headers=h)

    assert created.status_code == 201
    assert created.json()["name"] == "CSE 2027"
    assert created.json()["member_count"] == 0
    assert (duplicate.status_code, duplicate.json()["error"]["code"]) == (409, "batch_name_taken")
    assert renamed.json()["name"] == "CSE 2027 A"
    by_id = {b["id"]: b for b in listed.json()["items"]}
    assert by_id[str(org_setup.batch.id)]["member_count"] == 1  # counts in one query
    assert archived.json()["status"] == "archived"
    audit = await _audit(owner_sessionmaker, batch_id)
    assert [a.action for a in audit] == ["batch.created", "batch.updated", "batch.archived"]
    assert audit[1].before["name"] == "CSE 2027"
    assert audit[1].after["name"] == "CSE 2027 A"
    assert audit[0].actor_user_id == org_setup.admin.id


async def test_batch_members_emit_events(
    client: AsyncClient,
    org_setup: OrgSetup,
    factory: Factory,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    h = org_setup.h(org_setup.admin)
    s2 = await factory.member(org_setup.org, "student")
    batch_id = org_setup.batch.id

    added = await client.post(
        f"/api/v1/batches/{batch_id}/members",
        headers=h,
        json={"user_ids": [str(s2.id), str(org_setup.student.id)]},
    )
    listed = await client.get(f"/api/v1/batches/{batch_id}/members", headers=h)
    removed = await client.delete(f"/api/v1/batches/{batch_id}/members/{s2.id}", headers=h)
    removed_again = await client.delete(f"/api/v1/batches/{batch_id}/members/{s2.id}", headers=h)

    assert added.json() == {"added": [str(s2.id)], "already_members": [str(org_setup.student.id)]}
    assert {m["user"]["id"] for m in listed.json()["items"]} == {
        str(s2.id),
        str(org_setup.student.id),
    }
    assert removed.status_code == 204
    assert removed_again.status_code == 404
    events = await _events(owner_sessionmaker, batch_id)
    assert [(e.event_type, e.payload["user_id"]) for e in events] == [
        ("batch_member_added", str(s2.id)),
        ("batch_member_removed", str(s2.id)),
    ]
    assert events[0].payload == {
        "batch_id": str(batch_id),
        "user_id": str(s2.id),
        "organization_id": str(org_setup.org.id),
        "actor_user_id": str(org_setup.admin.id),
        "reason": "added",
    }
    assert events[0].organization_id == org_setup.org.id
    assert events[0].headers == {"version": 1}


async def test_only_org_members_can_join_batches(
    client: AsyncClient, org_setup: OrgSetup, factory: Factory
) -> None:
    outsider = await factory.member(await factory.org(), "student")
    response = await client.post(
        f"/api/v1/batches/{org_setup.batch.id}/members",
        headers=org_setup.h(org_setup.admin),
        json={"user_ids": [str(outsider.id)]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not_organization_members"


async def test_archived_batch_rejects_members(client: AsyncClient, org_setup: OrgSetup) -> None:
    h = org_setup.h(org_setup.admin)
    await client.delete(f"/api/v1/batches/{org_setup.batch.id}", headers=h)
    response = await client.post(
        f"/api/v1/batches/{org_setup.batch.id}/members",
        headers=h,
        json={"user_ids": [str(org_setup.instructor.id)]},
    )
    assert (response.status_code, response.json()["error"]["code"]) == (409, "batch_archived")


async def test_other_orgs_batches_are_404(
    client: AsyncClient, org_setup: OrgSetup, factory: Factory, auth_headers: AuthHeaders
) -> None:
    other = await factory.org()
    other_admin = await factory.member(other, "org_admin")
    h = auth_headers(other_admin, org=other.id)
    for method, path in [
        ("GET", f"/api/v1/batches/{org_setup.batch.id}"),
        ("PATCH", f"/api/v1/batches/{org_setup.batch.id}"),
        ("GET", f"/api/v1/batches/{org_setup.batch.id}/members"),
        ("DELETE", f"/api/v1/batches/{org_setup.batch.id}/members/{org_setup.student.id}"),
    ]:
        response = await client.request(method, path, headers=h, json={"name": "x"})
        assert response.status_code == 404, (method, path)


async def test_audit_log_records_ip_and_request_id(
    client: AsyncClient, org_setup: OrgSetup
) -> None:
    h = {**org_setup.h(org_setup.admin), "X-Request-ID": "req-audit-1"}
    created = await client.post("/api/v1/batches", headers=h, json={"name": "Audit me"})
    log = await client.get(
        f"/api/v1/audit-log?target_id={created.json()['id']}", headers=org_setup.h(org_setup.admin)
    )
    [entry] = log.json()["items"]
    assert entry["action"] == "batch.created"
    assert entry["request_id"] == "req-audit-1"
    assert entry["ip"] is not None
    assert entry["after"]["name"] == "Audit me"
