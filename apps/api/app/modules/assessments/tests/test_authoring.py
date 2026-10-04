"""Real HTTP authoring, ownership, validation, pagination and immutable publication."""

import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from app.modules.assessments.models import QuizVersionKey, QuizVersionQuestion
from app.modules.assessments.tests.authoring_helpers import SINGLE, AuthoredQuiz
from app.modules.audit.models import AuditLog
from tests.course_api import Campus, CourseApi, ok


async def test_required_quiz_and_missing_definition_block_publish(
    api: CourseApi, campus: Campus
) -> None:
    built = await api.build(campus.author, campus.p, modules=(("quiz",),))
    lesson = ok(
        await api.request(
            "GET", f"/courses/{built.id}/lessons/{built.lesson_ids[0]}", campus.author, campus.p
        )
    )
    assert lesson["is_required"] is True
    assert lesson["content"] == {}
    preview = ok(
        await api.request("GET", f"/courses/{built.id}/publish-preview", campus.author, campus.p)
    )
    assert preview["blockers"] == [
        {"code": "quiz_not_ready", "lesson_ids": [str(built.lesson_ids[0])]}
    ]
    response = await api.publish(campus.author, campus.p, built.id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "publish_blocked"


async def test_question_edit_tags_cursor_and_archives(authored: AuthoredQuiz) -> None:
    a = authored
    path = f"/question-banks/{a.bank_id}/questions"
    first = ok(await a.request("GET", f"{path}?limit=1"))
    second = ok(await a.request("GET", f"{path}?limit=1&cursor={first['next_cursor']}"))
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert (
        ok(await a.request("GET", f"{path}?cursor=broken"), 400)["error"]["code"]
        == "invalid_cursor"
    )
    assert len(ok(await a.request("GET", f"{path}?question_type=fill_blank"))["items"]) == 1
    skill = await a.api.factory.skill()
    qpath = f"/questions/{a.questions[0]['id']}"
    tagged = ok(await a.request("PUT", f"{qpath}/skills", json={"skill_ids": [str(skill.id)]}))
    assert tagged["skill_ids"] == [str(skill.id)]
    assert len(ok(await a.request("GET", f"{path}?skill_ids={skill.id}"))["items"]) == 1
    unknown = await a.request("PUT", f"{qpath}/skills", json={"skill_ids": [str(UUID(int=1))]})
    assert unknown.status_code == 422
    patched = ok(await a.request("PATCH", qpath, json={"prompt": "A corrected prompt"}))
    assert patched["revision"] == 3
    assert patched["prompt"] == "A corrected prompt"
    blank = ok(await a.request("GET", f"/questions/{a.questions[2]['id']}"))
    assert blank["answer_key"]["accepted_answers"] == ["Python", "PY"]
    ok(await a.request("DELETE", qpath), 204)
    assert len(ok(await a.request("GET", path))["items"]) == 2
    assert (await a.put()).status_code == 422


@pytest.mark.parametrize(
    "patch",
    [
        {"options": [{"id": "a", "text": "A"}, {"id": "a", "text": "B"}]},
        {"answer_key": {"correct_option_ids": ["unknown"]}},
        {"answer_key": {"correct_option_ids": ["a", "b"]}},
        {"prompt": None},
        {"question_type": "essay"},
    ],
)
async def test_invalid_question_change_rolls_back_revision(
    authored: AuthoredQuiz, patch: dict[str, Any]
) -> None:
    revision = await authored.revision()
    response = await authored.request(
        "PATCH", f"/questions/{authored.questions[0]['id']}", json=patch
    )
    assert response.status_code == 422
    assert await authored.revision() == revision


async def test_assignment_and_foreign_bank_cannot_be_used_as_quiz(authored: AuthoredQuiz) -> None:
    a = authored
    foreign = ok(
        await a.api.request(
            "POST",
            "/question-banks",
            a.campus.c_instructor,
            a.campus.c,
            json={"name": "Other org"},
            headers={"If-Match": "0"},
        ),
        201,
    )
    response = await a.put(
        selection={
            "mode": "bank",
            "bank_id": foreign["id"],
            "draw_count": 1,
            "marks_per_question": 6,
        }
    )
    assert response.status_code == 422
    assert foreign["id"] not in response.text
    for user, org in (
        (a.campus.c_instructor, a.campus.c),
        (a.campus.c_admin, a.campus.c),
        (a.campus.o_admin, a.campus.o),
    ):
        for path in (
            f"/question-banks/{a.bank_id}",
            f"/questions/{a.questions[0]['id']}",
            a.quiz_path,
        ):
            assert (await a.api.request("GET", path, user, org)).status_code == 404


async def test_snapshot_and_staff_preview_have_no_keys_and_copies_survive_edits(
    authored: AuthoredQuiz,
) -> None:
    a = authored
    version = ok(await a.publish(), 201)
    base = f"/courses/{a.course.id}/versions/{version['id']}"
    snapshot = ok(await a.request("GET", base))["snapshot"]
    lesson = snapshot["modules"][0]["lessons"][0]
    assert lesson["content"] == {"quiz_id": ok(await a.request("GET", a.quiz_path))["id"]}
    assert lesson["is_required"] is True
    assert len(lesson["assessment_structure"]["questions"]) == 3
    preview_path = f"{base}/lessons/{a.course.lesson_ids[0]}/quiz"
    old = ok(await a.request("GET", preview_path))
    for payload in (snapshot, old):
        text = json.dumps(payload)
        for secret in (
            "answer_key",
            "correct_option_ids",
            "accepted_answers",
            "explanation",
            "PRIVATE",
            "key_hash",
        ):
            assert secret not in text
    qpath = f"/questions/{a.questions[0]['id']}"
    ok(
        await a.request(
            "PATCH",
            qpath,
            json={
                "prompt": "New text",
                "answer_key": {"correct_option_ids": ["b"]},
                "explanation": "NEW PRIVATE",
            },
        )
    )
    assert ok(await a.request("GET", preview_path)) == old
    async with a.api.factory.sessionmaker() as reader:
        key = await reader.scalar(
            select(QuizVersionKey)
            .join(QuizVersionQuestion, QuizVersionQuestion.id == QuizVersionKey.question_id)
            .where(QuizVersionQuestion.question_id == UUID(a.questions[0]["id"]))
        )
        assert key is not None
        assert key.answer_key["correct_option_ids"] == ["a"]
        audits = await reader.scalars(
            select(AuditLog).where(AuditLog.organization_id == a.campus.p.id)
        )
        for audit in audits:
            assert "PRIVATE" not in str(audit.after)
            assert "correct_option_ids" not in str(audit.after)
    ok(await a.request("DELETE", f"/question-banks/{a.bank_id}"), 204)
    assert ok(await a.request("GET", preview_path)) == old
    assert (await a.preview())["blockers"][0]["code"] == "quiz_not_ready"


async def test_assigned_staff_public_preview_but_not_draft_or_author_keys(
    authored: AuthoredQuiz,
) -> None:
    a = authored
    version = ok(await a.publish(), 201)
    ok(await a.api.assign(a.campus.author, a.campus.p, a.course.id, to=a.campus.c), 201)
    public = (
        f"/courses/{a.course.id}/versions/{version['id']}/lessons/{a.course.lesson_ids[0]}/quiz"
    )
    for user in (a.campus.c_instructor, a.campus.c_admin):
        result = ok(await a.api.request("GET", public, user, a.campus.c))
        assert len(result["questions"]) == 3
        assert "answer_key" not in json.dumps(result)
        assert (await a.api.request("GET", a.quiz_path, user, a.campus.c)).status_code == 404
        assert (
            await a.api.request("GET", f"/questions/{a.questions[0]['id']}", user, a.campus.c)
        ).status_code == 404
    assert (await a.api.request("GET", public, a.campus.cse, a.campus.c)).status_code == 403
    assert (await a.api.request("GET", public, a.campus.o_admin, a.campus.o)).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        "pass_marks",
        "time_limit_seconds",
        "attempts_allowed",
        "marks",
        "membership",
        "key",
        "type",
        "option_identity",
    ],
)
async def test_structural_quiz_change_blocks_preview_and_minor(
    authored: AuthoredQuiz, change: str
) -> None:
    a = authored
    ok(await a.publish(), 201)
    qpath = f"/questions/{a.questions[0]['id']}"
    if change == "key":
        ok(await a.request("PATCH", qpath, json={"answer_key": {"correct_option_ids": ["b"]}}))
    elif change == "type":
        ok(await a.request("PATCH", qpath, json={"question_type": "mcq_multi"}))
    elif change == "option_identity":
        ok(
            await a.request(
                "PATCH",
                qpath,
                json={"options": [SINGLE["options"][0], {"id": "c", "text": "Second"}]},
            )
        )
    elif change in ("marks", "membership"):
        pairs = [dict(q) for q in a.body["selection"]["questions"]]
        if change == "marks":
            pairs[0]["marks"] = 3
        else:
            pairs.pop()
        ok(await a.put(selection={"mode": "manual", "questions": pairs}))
    else:
        ok(
            await a.put(
                **{
                    change: {"pass_marks": 3, "time_limit_seconds": 121, "attempts_allowed": 3}[
                        change
                    ]
                }
            )
        )
    preview = await a.preview()
    assert preview["minor_allowed"] is False
    expected = "quiz_grading_changed" if change == "key" else "quiz_structure_changed"
    assert expected in {c["code"] for c in preview["structural_changes"]}
    blocked = await a.publish("minor")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "minor_not_allowed"
    assert blocked.json()["error"]["details"] == preview["structural_changes"]
    major = ok(await a.publish("major"), 201)
    assert major["version"] == "2.0"


async def test_cosmetic_changes_are_minor_safe_and_frozen(authored: AuthoredQuiz) -> None:
    a = authored
    old = ok(await a.publish(), 201)
    qpath = f"/questions/{a.questions[0]['id']}"
    ok(
        await a.request(
            "PATCH",
            qpath,
            json={
                "prompt": "Corrected wording",
                "explanation": "PRIVATE correction",
                "options": [
                    {"id": "b", "text": "Corrected second"},
                    {"id": "a", "text": "Corrected first"},
                ],
            },
        )
    )
    skill = await a.api.factory.skill()
    ok(await a.request("PUT", f"{qpath}/skills", json={"skill_ids": [str(skill.id)]}))
    ok(
        await a.put(
            title="Corrected quiz",
            randomize_order=True,
            reveal_mode="explanations",
            reveal_timing="after_attempts_exhausted",
        )
    )
    assert (await a.preview())["structural_changes"] == []
    new = ok(await a.publish("minor"), 201)
    assert new["version"] == "1.1"
    old_path = f"/courses/{a.course.id}/versions/{old['id']}/lessons/{a.course.lesson_ids[0]}/quiz"
    assert ok(await a.request("GET", old_path))["reveal_mode"] == "score_only"


async def test_bank_draw_pool_membership_filters_and_count_are_structural(
    authored: AuthoredQuiz,
) -> None:
    a = authored
    selection = {"mode": "bank", "bank_id": a.bank_id, "draw_count": 2, "marks_per_question": 3}
    ok(await a.put(selection=selection))
    version = ok(await a.publish(), 201)
    path = f"/courses/{a.course.id}/versions/{version['id']}/lessons/{a.course.lesson_ids[0]}/quiz"
    assert len(ok(await a.request("GET", path))["questions"]) == 3
    assert ok(await a.request("GET", path))["max_marks"] == "6.00"
    # A frozen draw pool includes all eligible candidates, not a publication-time random sample.
    ok(
        await a.request(
            "POST", f"/question-banks/{a.bank_id}/questions", json={**SINGLE, "prompt": "Fourth"}
        ),
        201,
    )
    assert (await a.preview())["minor_allowed"] is False
    assert len(ok(await a.request("GET", path))["questions"]) == 3
    ok(await a.publish(), 201)
    ok(await a.put(selection={**selection, "draw_count": 1, "marks_per_question": 6}))
    assert (await a.preview())["minor_allowed"] is False


async def test_outline_cannot_forge_quiz_reference(authored: AuthoredQuiz) -> None:
    a = authored
    path = f"/courses/{a.course.id}/lessons/{a.course.lesson_ids[0]}"
    response = await a.api.request(
        "PATCH", path, a.campus.author, a.campus.p, json={"content": {"quiz_id": str(UUID(int=1))}}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "quiz_content_managed_separately"
