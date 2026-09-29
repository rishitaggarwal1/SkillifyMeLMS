"""Course builder API: the draft tree, ordering, revisions, lesson content and access rules."""

from uuid import UUID

from tests.course_api import Campus, CourseApi, ok


async def test_author_builds_a_course(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("video", "notes"), ("pdf", "quiz")])
    draft = ok(await api.request("GET", f"/courses/{course.id}/draft", campus.author, campus.p))

    assert draft["course"]["is_owner"] is True
    assert draft["course"]["current_version"] is None
    assert [m["position"] for m in draft["modules"]] == [1, 2]
    lessons = [lesson for m in draft["modules"] for lesson in m["lessons"]]
    assert [lesson["lesson_type"] for lesson in lessons] == ["video", "notes", "pdf", "quiz"]
    assert [lesson["position"] for lesson in lessons] == [1, 2, 1, 2]
    # Placeholders never count toward progress until their phase lands.
    assert [lesson["is_required"] for lesson in lessons] == [True, True, True, False]
    assert draft["course"]["revision"] == 1 + 2 + 4  # created, 2 modules, 4 lessons


async def test_slug_is_derived_and_unique_per_org(api: CourseApi, campus: Campus) -> None:
    first = ok(
        await api.request("POST", "/courses", campus.author, campus.p, json={"title": "DSA 101!"}),
        201,
    )
    again = await api.request(
        "POST", "/courses", campus.author, campus.p, json={"title": "DSA 101"}
    )
    elsewhere = await api.request(
        "POST", "/courses", campus.c_instructor, campus.c, json={"title": "DSA 101"}
    )

    assert first["slug"] == "dsa-101"
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "slug_taken"
    assert elsewhere.status_code == 201


async def test_reorder_modules_and_move_lessons(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes", "notes"), ("pdf",)])
    m1, m2 = course.module_ids
    a, b, c = course.lesson_ids

    ok(
        await api.request(
            "PUT", f"/courses/{course.id}/modules/order", campus.author, campus.p,
            json={"ids": [str(m2), str(m1)]},
        )
    )  # fmt: skip
    # Move lesson `b` from module 1 to the front of module 2.
    draft = ok(
        await api.request(
            "PUT", f"/courses/{course.id}/modules/{m2}/lessons/order", campus.author, campus.p,
            json={"ids": [str(b), str(c)]},
        )
    )  # fmt: skip

    outline = {UUID(m["id"]): [UUID(x["id"]) for x in m["lessons"]] for m in draft["modules"]}
    assert [UUID(m["id"]) for m in draft["modules"]] == [m2, m1]
    assert outline == {m2: [b, c], m1: [a]}
    positions = {x["id"]: x["position"] for m in draft["modules"] for x in m["lessons"]}
    assert positions == {str(b): 1, str(c): 2, str(a): 1}


async def test_reorder_must_list_every_item(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes", "notes"), ("notes",)])
    m1, _m2 = course.module_ids
    missing_module = await api.request(
        "PUT", f"/courses/{course.id}/modules/order", campus.author, campus.p,
        json={"ids": [str(m1)]},
    )  # fmt: skip
    dropped_lesson = await api.request(
        "PUT", f"/courses/{course.id}/modules/{m1}/lessons/order", campus.author, campus.p,
        json={"ids": [str(course.lesson_ids[0])]},
    )  # fmt: skip
    foreign = await api.build(campus.author, campus.p, [("notes",)])
    other_course_lesson = await api.request(
        "PUT", f"/courses/{course.id}/modules/{m1}/lessons/order", campus.author, campus.p,
        json={"ids": [*map(str, course.lesson_ids[:2]), str(foreign.lesson_ids[0])]},
    )  # fmt: skip

    for response in (missing_module, dropped_lesson, other_course_lesson):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "order_mismatch"


async def test_stale_revision_is_rejected(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes",)])
    revision = ok(await api.request("GET", f"/courses/{course.id}", campus.author, campus.p))[
        "revision"
    ]
    path = f"/courses/{course.id}/modules/{course.module_ids[0]}"

    first = await api.request(
        "PATCH", path, campus.author, campus.p, json={"title": "A"},
        headers={"If-Match": str(revision)},
    )  # fmt: skip
    stale = await api.request(
        "PATCH", path, campus.author, campus.p, json={"title": "B"},
        headers={"If-Match": f'"{revision}"'},
    )  # fmt: skip

    assert ok(first)["course_revision"] == revision + 1
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "revision_conflict"


async def test_outline_edits_require_if_match(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes",)])
    base = f"/api/v1/courses/{course.id}"
    module, lesson = course.module_ids[0], course.lesson_ids[0]
    before = await api.factory.course_revision(course.id)
    edits = [
        ("POST", "/modules", {"title": "M"}),
        ("PUT", "/modules/order", {"ids": [str(module)]}),
        ("PATCH", f"/modules/{module}", {"title": "M2"}),
        ("DELETE", f"/modules/{module}", None),
        ("POST", f"/modules/{module}/lessons", {"title": "L", "lesson_type": "notes"}),
        ("PUT", f"/modules/{module}/lessons/order", {"ids": [str(lesson)]}),
        ("PATCH", f"/lessons/{lesson}", {"title": "L2"}),
        ("DELETE", f"/lessons/{lesson}", None),
        ("PUT", f"/lessons/{lesson}/skills", {"skill_ids": []}),
    ]

    for method, suffix, body in edits:
        response = await api.client.request(
            method, base + suffix, headers=api.h(campus.author, campus.p), json=body
        )
        assert response.status_code == 428, (method, suffix, response.text)
        assert response.json()["error"]["code"] == "precondition_required"

    assert await api.factory.course_revision(course.id) == before
    # Course details (not the outline) keep If-Match optional.
    details = await api.request(
        "PATCH", f"/courses/{course.id}", campus.author, campus.p, json={"title": "Renamed"}
    )
    assert ok(details)["title"] == "Renamed"


async def test_if_match_is_checked_after_authentication(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes",)])
    response = await api.client.post(f"/api/v1/courses/{course.id}/modules", json={"title": "M"})
    assert response.status_code == 401


async def test_lesson_content_is_validated_per_type(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [()])
    path = f"/courses/{course.id}/modules/{course.module_ids[0]}/lessons"
    other_orgs_video = await api.factory.video(campus.c, status="ready")

    wrong_shape = await api.request(
        "POST", path, campus.author, campus.p,
        json={"title": "V", "lesson_type": "video", "content": {"file_id": str(UUID(int=1))}},
    )  # fmt: skip
    foreign_video = await api.request(
        "POST", path, campus.author, campus.p,
        json={"title": "V", "lesson_type": "video",
              "content": {"video_asset_id": str(other_orgs_video.id)}},
    )  # fmt: skip
    quiz = ok(
        await api.request(
            "POST", path, campus.author, campus.p,
            json={"title": "Q", "lesson_type": "quiz", "is_required": True,
                  "completion_threshold": "0.5"},
        ),
        201,
    )  # fmt: skip

    assert wrong_shape.json()["error"]["code"] == "invalid_lesson_content"
    assert foreign_video.json()["error"]["code"] == "invalid_video"
    assert quiz["is_required"] is False
    assert quiz["completion_threshold"] is None


async def test_lesson_skill_tags(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes",)])
    skills = ok(await api.request("GET", "/skills?under=dsa&limit=3", campus.author, campus.p))
    skill_ids = [s["id"] for s in skills["items"]]
    path = f"/courses/{course.id}/lessons/{course.lesson_ids[0]}/skills"

    tagged = ok(
        await api.request("PUT", path, campus.author, campus.p, json={"skill_ids": skill_ids})
    )
    unknown = await api.request(
        "PUT", path, campus.author, campus.p, json={"skill_ids": [str(UUID(int=7))]}
    )

    assert sorted(tagged["skill_ids"]) == sorted(skill_ids)
    assert unknown.json()["error"]["code"] == "unknown_skills"


async def test_delete_lesson_and_module_renumber(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes", "notes", "notes"), ("notes",)])
    ok(
        await api.request(
            "DELETE", f"/courses/{course.id}/lessons/{course.lesson_ids[0]}", campus.author,
            campus.p,
        )
    )  # fmt: skip
    ok(
        await api.request(
            "DELETE", f"/courses/{course.id}/modules/{course.module_ids[0]}", campus.author,
            campus.p,
        )
    )  # fmt: skip
    draft = ok(await api.request("GET", f"/courses/{course.id}/draft", campus.author, campus.p))

    assert [(m["id"], m["position"]) for m in draft["modules"]] == [(str(course.module_ids[1]), 1)]


async def test_assigned_org_cannot_edit_and_unassigned_org_cannot_see(
    api: CourseApi, campus: Campus
) -> None:
    course = await api.build(campus.author, campus.p, [("notes",)])
    ok(await api.publish(campus.author, campus.p, course.id), 201)
    ok(await api.assign(campus.author, campus.p, course.id, to=campus.c), 201)

    # The assigned org reads the published course...
    assert (
        ok(await api.request("GET", f"/courses/{course.id}", campus.c_admin, campus.c))["is_owner"]
        is False
    )
    # ...but every edit is a 404 (existence of the draft isn't revealed).
    for method, path, body in (
        ("PATCH", f"/courses/{course.id}", {"title": "Hijacked"}),
        ("GET", f"/courses/{course.id}/draft", None),
        ("POST", f"/courses/{course.id}/modules", {"title": "M"}),
        ("DELETE", f"/courses/{course.id}/lessons/{course.lesson_ids[0]}", None),
        ("POST", f"/courses/{course.id}/versions", {"release_type": "major"}),
    ):
        response = await api.request(method, path, campus.c_admin, campus.c, json=body)
        assert response.status_code == 404, (method, path, response.text)
    # The unassigned org can't even read it.
    assert (
        await api.request("GET", f"/courses/{course.id}", campus.o_admin, campus.o)
    ).status_code == 404


async def test_course_lists(api: CourseApi, campus: Campus) -> None:
    mine = await api.build(campus.c_instructor, campus.c, [("notes",)], title="Own")
    granted = await api.build(campus.author, campus.p, [("notes",)], title="Granted")
    draft_only = await api.build(campus.author, campus.p, [("notes",)], title="Draft")
    ok(await api.publish(campus.author, campus.p, granted.id), 201)
    ok(await api.assign(campus.author, campus.p, granted.id, to=campus.c), 201)

    def ids(page: dict[str, list[dict[str, str]]]) -> set[str]:
        return {c["id"] for c in page["items"]}

    everything = ids(ok(await api.request("GET", "/courses", campus.c_admin, campus.c)))
    owned = ids(ok(await api.request("GET", "/courses?owned=true", campus.c_admin, campus.c)))
    assigned = ids(ok(await api.request("GET", "/courses?owned=false", campus.c_admin, campus.c)))

    assert everything == {str(mine.id), str(granted.id)}
    assert owned == {str(mine.id)}
    assert assigned == {str(granted.id)}
    assert str(draft_only.id) not in everything


async def test_archived_course_cannot_change(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes",)])
    archived = ok(await api.request("DELETE", f"/courses/{course.id}", campus.author, campus.p))
    add = await api.request(
        "POST", f"/courses/{course.id}/modules", campus.author, campus.p, json={"title": "M"}
    )

    assert archived["status"] == "archived"
    assert add.json()["error"]["code"] == "course_archived"
