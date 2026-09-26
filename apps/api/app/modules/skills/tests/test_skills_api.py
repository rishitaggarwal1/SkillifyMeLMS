"""Skills taxonomy API: everyone reads; platform admins and content-publisher staff write."""

from uuid import UUID

from uuid_utils.compat import uuid7

from tests.course_api import Campus, CourseApi, ok


async def test_everyone_lists_and_filters_the_taxonomy(api: CourseApi, campus: Campus) -> None:
    dsa = ok(await api.request("GET", "/skills?under=dsa&limit=100", campus.cse, campus.c))
    search = ok(await api.request("GET", "/skills?q=network", campus.cse, campus.c))
    bad_path = await api.request("GET", "/skills?under=DSA;drop", campus.cse, campus.c)

    paths = {s["path"] for s in dsa["items"]}
    assert {"dsa", "dsa.arrays", "dsa.graphs"} <= paths
    assert all(p == "dsa" or p.startswith("dsa.") for p in paths)
    assert [s["path"] for s in search["items"]] == ["core_cs.computer_networks"]
    assert bad_path.status_code == 422


async def test_publisher_staff_create_and_edit_skills(api: CourseApi, campus: Campus) -> None:
    arrays = ok(await api.request("GET", "/skills?under=dsa.arrays", campus.author, campus.p))
    parent_id = next(s["id"] for s in arrays["items"] if s["path"] == "dsa.arrays")
    slug = f"two_pointers_{uuid7().hex[-6:]}"

    created = ok(
        await api.request(
            "POST", "/skills", campus.author, campus.p,
            json={"parent_id": parent_id, "name": "Two Pointers", "slug": slug},
        ),
        201,
    )  # fmt: skip
    duplicate = await api.request(
        "POST", "/skills", campus.p_admin, campus.p,
        json={"parent_id": parent_id, "name": "Again", "slug": slug},
    )  # fmt: skip
    edited = ok(
        await api.request(
            "PATCH", f"/skills/{created['id']}", campus.p_admin, campus.p,
            json={"description": "Two indices walking an array"},
        )
    )  # fmt: skip

    assert created["path"] == f"dsa.arrays.{slug}"
    assert created["parent_id"] == parent_id
    assert duplicate.json()["error"]["code"] == "skill_exists"
    assert edited["description"] == "Two indices walking an array"


async def test_college_staff_and_students_cannot_edit_skills(
    api: CourseApi, campus: Campus
) -> None:
    body = {"name": "Nope", "slug": f"nope_{uuid7().hex[-6:]}"}
    college_admin = await api.request("POST", "/skills", campus.c_admin, campus.c, json=body)
    student = await api.request("POST", "/skills", campus.p_student, campus.p, json=body)
    unknown_parent = await api.request(
        "POST", "/skills", campus.author, campus.p, json={**body, "parent_id": str(UUID(int=9))}
    )

    assert college_admin.status_code == 403
    assert college_admin.json()["error"]["code"] == "content_publisher_required"
    assert student.status_code == 403
    assert unknown_parent.status_code == 404


async def test_platform_admin_manages_skills_from_any_org(api: CourseApi, campus: Campus) -> None:
    headers = api.auth_headers(campus.platform_admin, org=campus.c.id, platform_admin=True)
    response = await api.client.post(
        "/api/v1/skills",
        headers=headers,
        json={"name": "System Design", "slug": f"system_design_{uuid7().hex[-6:]}"},
    )
    assert response.status_code == 201, response.text
