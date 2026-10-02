"""The organization directory: content-publisher staff find orgs to assign courses to."""

from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import text
from uuid_utils.compat import uuid7

from tests.factories import Factory
from tests.fixtures import AuthHeaders, TenantSessionFactory


async def test_publisher_staff_browse_active_orgs_by_name(
    client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    tag = uuid7().hex[-8:]
    publisher = await factory.org(name=f"Pub {tag}", publisher=True)
    staff = await factory.member(publisher, "instructor")
    names = [f"Dir {tag} College {n}" for n in ("C", "a", "B")]
    colleges = {name: await factory.org(name=name) for name in names}
    await factory.org(name=f"Dir {tag} Closed", status="archived")
    h = auth_headers(staff, org=publisher.id)

    first = await client.get(f"/api/v1/organizations/directory?q=dir%20{tag}&limit=2", headers=h)
    assert first.status_code == 200, first.text
    page1 = first.json()
    assert [o["name"] for o in page1["items"]] == [f"Dir {tag} College a", f"Dir {tag} College B"]
    assert set(page1["items"][0]) == {"id", "name"}  # nothing else about other tenants
    page2 = (
        await client.get(
            f"/api/v1/organizations/directory?q=dir%20{tag}&limit=2&cursor={page1['next_cursor']}",
            headers=h,
        )
    ).json()
    assert [o["name"] for o in page2["items"]] == [f"Dir {tag} College C"]  # archived excluded
    assert page2["next_cursor"] is None

    wanted = colleges[f"Dir {tag} College B"].id
    by_id = (await client.get(f"/api/v1/organizations/directory?ids={wanted}", headers=h)).json()
    assert [UUID(o["id"]) for o in by_id["items"]] == [wanted]
    # A search is a plain substring, never a LIKE pattern.
    wild = await client.get("/api/v1/organizations/directory?q=%25", headers=h)
    assert all("%" in o["name"] for o in wild.json()["items"])


async def test_only_publisher_staff_and_platform_admins(
    client: AsyncClient, factory: Factory, auth_headers: AuthHeaders
) -> None:
    publisher = await factory.org(publisher=True)
    college = await factory.org()
    cases = [
        (await factory.member(publisher, "student"), publisher, 403),
        (await factory.member(publisher, "lab_author"), publisher, 403),
        (await factory.member(college, "org_admin"), college, 403),  # not a publisher
        (await factory.member(publisher, "org_admin"), publisher, 200),
    ]
    for user, org, expected in cases:
        response = await client.get(
            "/api/v1/organizations/directory", headers=auth_headers(user, org=org.id)
        )
        assert response.status_code == expected, (org.name, response.text)
        if expected == 403:
            error = response.json()["error"]
            assert error["code"] == "content_publisher_staff_required"
            assert error["message"] == "You do not have permission to perform this action."
    admin = await factory.user()
    response = await client.get(
        "/api/v1/organizations/directory", headers=auth_headers(admin, platform_admin=True)
    )
    assert response.status_code == 200


async def test_the_sql_function_itself_returns_nothing_to_others(
    factory: Factory, tenant_session: TenantSessionFactory
) -> None:
    college = await factory.org()
    admin = await factory.member(college, "org_admin")
    async with tenant_session(org=college.id, user=admin.id) as session:
        rows = (
            await session.execute(
                text("SELECT * FROM app.organization_directory(NULL, NULL, NULL, NULL, 100)")
            )
        ).all()
    assert rows == []  # RLS-equivalent: the database refuses, not only the service
