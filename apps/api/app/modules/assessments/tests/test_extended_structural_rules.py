"""Every frozen grading-rule identity matters; cosmetic/late-policy corrections do not."""

from copy import deepcopy
from typing import Any

import pytest

from app.db.base import new_id
from app.modules.assessments.models import Question, QuestionKey
from app.modules.assessments.tests.authoring_helpers import SINGLE, AuthoredQuiz
from app.modules.courses import content_sources
from app.modules.courses.versioning import structural_changes
from tests.course_api import ok


@pytest.mark.parametrize(
    "key",
    [
        {"accepted_answers": ["Python", "PY", "python3"]},
        {"accepted_answers": ["Python", "PY"], "case_sensitive": True},
    ],
)
async def test_blank_grading_rules_block_minor(authored: AuthoredQuiz, key: dict[str, Any]) -> None:
    a = authored
    ok(await a.publish(), 201)
    ok(await a.request("PATCH", f"/questions/{a.questions[2]['id']}", json={"answer_key": key}))
    assert (await a.preview())["structural_changes"] == [
        {"code": "quiz_grading_changed", "lesson_ids": [str(a.course.lesson_ids[0])]}
    ]
    assert (await a.publish("minor")).status_code == 409


async def test_blank_alias_order_normalization_is_minor_safe(authored: AuthoredQuiz) -> None:
    a = authored
    ok(await a.publish(), 201)
    ok(
        await a.request(
            "PATCH",
            f"/questions/{a.questions[2]['id']}",
            json={"answer_key": {"accepted_answers": [" py ", "python"]}},
        )
    )
    assert (await a.preview())["structural_changes"] == []
    assert ok(await a.publish("minor"), 201)["version"] == "1.1"


async def test_bank_filters_and_tag_changes_affect_the_frozen_pool(authored: AuthoredQuiz) -> None:
    a = authored
    skill = await a.api.factory.skill()
    for question in a.questions[:2]:
        ok(
            await a.request(
                "PUT", f"/questions/{question['id']}/skills", json={"skill_ids": [str(skill.id)]}
            )
        )
    selection = {
        "mode": "bank",
        "bank_id": a.bank_id,
        "draw_count": 2,
        "marks_per_question": 3,
        "skill_ids": [str(skill.id)],
    }
    ok(await a.put(selection=selection))
    version = ok(await a.publish(), 201)
    path = f"/courses/{a.course.id}/versions/{version['id']}/lessons/{a.course.lesson_ids[0]}/quiz"
    assert len(ok(await a.request("GET", path))["questions"]) == 2
    ok(
        await a.request(
            "PUT", f"/questions/{a.questions[2]['id']}/skills", json={"skill_ids": [str(skill.id)]}
        )
    )
    assert (await a.preview())["minor_allowed"] is False
    assert (await a.publish("minor")).status_code == 409
    ok(await a.publish(), 201)
    ok(await a.put(selection={**selection, "skill_ids": []}))
    # Even when membership is unchanged, changing the pool's filter is structural.
    assert (await a.preview())["minor_allowed"] is False


def assignment_snapshot() -> dict[str, Any]:
    return {
        "modules": [
            {
                "id": "module",
                "lessons": [
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "lesson_type": "assignment",
                        "is_required": True,
                        "completion_threshold": None,
                        "content": {
                            "max_marks": 10,
                            "submission_kinds": ["text"],
                            "rubric": {
                                "criteria": [
                                    {"id": "correctness", "max_marks": 10, "label": "Correctness"}
                                ]
                            },
                            "late_policy": {"mode": "accept", "percent_per_day": 0},
                        },
                    }
                ],
            }
        ]
    }


@pytest.mark.parametrize(
    "field", ["max_marks", "submission_kinds", "criterion_id", "criterion_max"]
)
def test_assignment_structure_in_publish_diff(field: str) -> None:
    old = assignment_snapshot()
    new = deepcopy(old)
    content = new["modules"][0]["lessons"][0]["content"]
    if field == "max_marks":
        content["max_marks"] = 11
    elif field == "submission_kinds":
        content["submission_kinds"] = ["text", "file"]
    else:
        content["rubric"]["criteria"][0]["id" if field == "criterion_id" else "max_marks"] = (
            "renamed" if field == "criterion_id" else 11
        )
    assert "assignment_structure_changed" in {c.code for c in structural_changes(old, new)}


def test_assignment_late_mode_rate_and_rubric_wording_are_minor_safe() -> None:
    old = assignment_snapshot()
    new = deepcopy(old)
    content = new["modules"][0]["lessons"][0]["content"]
    content["late_policy"] = {"mode": "penalty", "percent_per_day": 10}
    content["rubric"]["criteria"][0]["label"] = "Clearer wording"
    assert structural_changes(old, new) == []


async def test_pool_limit_is_fail_closed_and_filters_narrow_it(authored: AuthoredQuiz) -> None:
    a = authored
    rows = [
        Question(
            id=new_id(),
            organization_id=a.campus.p.id,
            bank_id=a.questions[0]["bank_id"],
            question_type="mcq_single",
            prompt="Pool candidate",
            options=SINGLE["options"],
        )
        for _ in range(1001)
    ]
    await a.api.factory._save(*rows)
    await a.api.factory._save(
        *[
            QuestionKey(
                question_id=q.id, organization_id=q.organization_id, answer_key=SINGLE["answer_key"]
            )
            for q in rows
        ]
    )
    revision = await a.api.factory.course_revision(a.course.id)
    selection = {"mode": "bank", "bank_id": a.bank_id, "draw_count": 1, "marks_per_question": 6}
    assert (await a.put(selection=selection)).status_code == 422
    assert await a.api.factory.course_revision(a.course.id) == revision
    assert ok(await a.request("GET", a.quiz_path))["selection"]["mode"] == "manual"
    skill = await a.api.factory.skill()
    ok(
        await a.request(
            "PUT", f"/questions/{a.questions[0]['id']}/skills", json={"skill_ids": [str(skill.id)]}
        )
    )
    ok(await a.put(selection={**selection, "skill_ids": [str(skill.id)]}))
    version = ok(await a.publish(), 201)
    path = f"/courses/{a.course.id}/versions/{version['id']}/lessons/{a.course.lesson_ids[0]}/quiz"
    assert len(ok(await a.request("GET", path))["questions"]) == 1


async def test_quiz_source_registration_fails_closed(
    authored: AuthoredQuiz, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(content_sources, "_SOURCES", {})
    monkeypatch.setattr(content_sources, "_load_wiring", lambda: None)
    a = authored
    for response in (
        await a.api.request(
            "GET", f"/courses/{a.course.id}/publish-preview", a.campus.author, a.campus.p
        ),
        await a.publish(),
    ):
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "content_source_not_registered"
        assert response.json()["error"]["details"] == {"lesson_type": "quiz"}
    assert ok(await a.request("GET", f"/courses/{a.course.id}/versions"))["items"] == []
