"""Publishing and versioning: snapshots, 1.0 / major / minor numbering, and the minor rule."""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.courses.versioning import structural_changes
from tests.course_api import Campus, CourseApi, ok

# ---------------------------------------------------------------------------- pure diff


def _id(name: str) -> str:
    """Stable UUID text per short name, so the cases below stay readable."""
    return str(UUID(int=sum(ord(ch) << (8 * i) for i, ch in enumerate(name))))


def _snap(*modules: tuple[str, list[tuple[str, str, bool, str | None]]]) -> dict[str, Any]:
    return {
        "modules": [
            {
                "id": _id(module_id),
                "lessons": [
                    {"id": _id(i), "lesson_type": t, "is_required": r, "completion_threshold": th}
                    for i, t, r, th in lessons
                ],
            }
            for module_id, lessons in modules
        ]
    }


A = ("a", "notes", True, None)
B = ("b", "video", True, "0.90")
C = ("c", "pdf", True, None)
BASE = _snap(("m1", [A, B]), ("m2", [C]))


@pytest.mark.parametrize(
    ("draft", "codes"),
    [
        (BASE, []),
        (_snap(("m2", [C]), ("m1", [A, B])), ["modules_changed"]),
        (_snap(("m1", [A, B]), ("m2", [C]), ("m3", [])), ["modules_changed"]),
        (_snap(("m1", [A, B]), ("m2", [C, ("d", "notes", True, None)])), ["lessons_added"]),
        (_snap(("m1", [A]), ("m2", [C])), ["lessons_removed"]),
        (_snap(("m1", [B, A]), ("m2", [C])), ["lessons_reordered"]),
        (_snap(("m1", [A]), ("m2", [B, C])), ["lessons_reordered"]),  # moved between modules
        (
            _snap(("m1", [A, ("b", "video", True, "0.50")]), ("m2", [C])),
            ["lesson_settings_changed"],
        ),
        (_snap(("m1", [A, B]), ("m2", [("c", "pdf", False, None)])), ["lesson_settings_changed"]),
    ],
)
def test_structural_changes(draft: dict[str, Any], codes: list[str]) -> None:
    assert [c.code for c in structural_changes(BASE, draft)] == codes


def test_structural_changes_name_the_lessons() -> None:
    changes = structural_changes(BASE, _snap(("m1", [A]), ("m2", [B, C])))
    assert [(c.code, [str(i) for i in c.lesson_ids]) for c in changes] == [
        ("lessons_reordered", [_id("b"), _id("c")])  # b moved in; c's position changed
    ]


# ---------------------------------------------------------------------------- publishing API


async def _version(api: CourseApi, campus: Campus, course_id: UUID, kind: str = "major") -> str:
    return str(ok(await api.publish(campus.author, campus.p, course_id, kind), 201)["version"])


async def test_first_publish_is_1_0_then_minor_and_major(api: CourseApi, campus: Campus) -> None:
    course = await api.build(campus.author, campus.p, [("notes", "pdf")])
    # The first release is always 1.0, even if "minor" is requested.
    assert await _version(api, campus, course.id, "minor") == "1.0"

    ok(
        await api.request(
            "PATCH", f"/courses/{course.id}/lessons/{course.lesson_ids[0]}", campus.author,
            campus.p, json={"title": "Corrected title"},
        )
    )  # fmt: skip
    preview = ok(
        await api.request("GET", f"/courses/{course.id}/publish-preview", campus.author, campus.p)
    )
    assert preview == {
        "is_first_release": False, "next_major": "2.0", "next_minor": "1.1",
        "minor_allowed": True, "structural_changes": [], "blockers": [],
    }  # fmt: skip
    assert await _version(api, campus, course.id, "minor") == "1.1"
    assert await _version(api, campus, course.id, "major") == "2.0"
    assert await _version(api, campus, course.id, "minor") == "2.1"

    course_out = ok(await api.request("GET", f"/courses/{course.id}", campus.author, campus.p))
    assert course_out["current_version"]["version"] == "2.1"
    versions = ok(
        await api.request("GET", f"/courses/{course.id}/versions", campus.author, campus.p)
    )
    assert [v["version"] for v in versions["items"]] == ["2.1", "2.0", "1.1", "1.0"]


@pytest.mark.parametrize("change", ["add", "remove", "reorder", "move", "settings"])
async def test_minor_rejected_for_structural_changes(
    api: CourseApi, campus: Campus, change: str
) -> None:
    course = await api.build(campus.author, campus.p, [("notes", "notes"), ("pdf",)])
    await _version(api, campus, course.id)
    m1, m2 = course.module_ids
    a, b, c = course.lesson_ids
    requests = {
        "add": ("POST", f"/courses/{course.id}/modules/{m2}/lessons",
                {"title": "New", "lesson_type": "notes"}),
        "remove": ("DELETE", f"/courses/{course.id}/lessons/{b}", None),
        "reorder": ("PUT", f"/courses/{course.id}/modules/{m1}/lessons/order",
                    {"ids": [str(b), str(a)]}),
        "move": ("PUT", f"/courses/{course.id}/modules/{m2}/lessons/order",
                 {"ids": [str(c), str(b)]}),
        "settings": ("PATCH", f"/courses/{course.id}/lessons/{a}", {"is_required": False}),
    }  # fmt: skip
    method, path, body = requests[change]
    assert (await api.request(method, path, campus.author, campus.p, json=body)).is_success

    preview = ok(
        await api.request("GET", f"/courses/{course.id}/publish-preview", campus.author, campus.p)
    )
    minor = await api.publish(campus.author, campus.p, course.id, "minor")

    assert preview["minor_allowed"] is False
    assert minor.status_code == 409
    assert minor.json()["error"]["code"] == "minor_not_allowed"
    assert await _version(api, campus, course.id, "major") == "2.0"


async def test_publish_blockers(api: CourseApi, campus: Campus) -> None:
    empty = await api.build(campus.author, campus.p, [()])
    course = await api.build(campus.author, campus.p, [("notes",)])
    module = course.module_ids[0]
    pending_video = await api.factory.video(campus.p, status="processing")
    video_lesson = ok(
        await api.request(
            "POST", f"/courses/{course.id}/modules/{module}/lessons", campus.author, campus.p,
            json={"title": "V", "lesson_type": "video",
                  "content": {"video_asset_id": str(pending_video.id)}},
        ),
        201,
    )  # fmt: skip
    pdf_lesson = ok(
        await api.request(
            "POST", f"/courses/{course.id}/modules/{module}/lessons", campus.author, campus.p,
            json={"title": "P", "lesson_type": "pdf"},
        ),
        201,
    )  # fmt: skip

    empty_publish = await api.publish(campus.author, campus.p, empty.id)
    blocked = await api.publish(campus.author, campus.p, course.id)

    assert empty_publish.status_code == 409
    assert empty_publish.json()["error"]["details"] == [{"code": "empty_course", "lesson_ids": []}]
    assert blocked.json()["error"]["code"] == "publish_blocked"
    assert blocked.json()["error"]["details"] == [
        {"code": "video_not_ready", "lesson_ids": [video_lesson["id"]]},
        {"code": "pdf_not_ready", "lesson_ids": [pdf_lesson["id"]]},
    ]


async def test_snapshot_is_immutable_and_isolated_from_draft_edits(
    api: CourseApi, campus: Campus, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    course = await api.build(campus.author, campus.p, [("video", "notes")])
    version = ok(await api.publish(campus.author, campus.p, course.id), 201)
    ok(
        await api.request(
            "PATCH", f"/courses/{course.id}/lessons/{course.lesson_ids[1]}", campus.author,
            campus.p, json={"title": "Edited later"},
        )
    )  # fmt: skip

    detail = ok(
        await api.request(
            "GET", f"/courses/{course.id}/versions/{version['id']}", campus.author, campus.p
        )
    )
    lessons = detail["snapshot"]["modules"][0]["lessons"]
    assert [lesson["title"] for lesson in lessons] == ["video lesson", "notes lesson"]
    assert lessons[0]["video_duration_seconds"] == 120
    assert lessons[0]["completion_threshold"] is None

    async with owner_sessionmaker() as s:
        rows = await s.execute(
            text(
                "SELECT lesson_id, lesson_type::text, is_required, video_duration_seconds "
                "FROM course_version_lessons WHERE version_id = :v ORDER BY position"
            ),
            {"v": version["id"]},
        )
        assert [(r[1], r[2], r[3]) for r in rows] == [("video", True, 120), ("notes", True, None)]


async def test_publish_writes_catalog_event_and_audit(
    api: CourseApi, campus: Campus, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    course = await api.build(campus.author, campus.p, [("notes",)], public=True)
    version = ok(await api.publish(campus.author, campus.p, course.id), 201)

    async with owner_sessionmaker() as s:
        catalog = (
            await s.execute(
                text("SELECT version_id, lesson_count FROM catalog_entries WHERE course_id = :c"),
                {"c": course.id},
            )
        ).one()
        event = (
            await s.execute(
                text(
                    "SELECT event_type, payload FROM outbox_events "
                    "WHERE aggregate_type = 'course' AND aggregate_id = :c"
                ),
                {"c": course.id},
            )
        ).one()
        audit = await s.scalar(
            text(
                "SELECT count(*) FROM audit_log "
                "WHERE action = 'course.published' AND target_id = :c"
            ),
            {"c": str(course.id)},
        )

    assert (str(catalog[0]), catalog[1]) == (version["id"], 1)
    assert event[0] == "course_published"
    assert event[1]["version_id"] == version["id"]
    assert (event[1]["major"], event[1]["minor"]) == (1, 0)
    assert audit == 1
