"""Secrecy tests using privileged inputs, independently of DB/RLS."""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.db.base import new_id
from app.modules.assessments.schemas import (
    ActiveQuestion,
    AnswersResult,
    ExplanationsResult,
    ResultContext,
    RevealMode,
    RevealTiming,
)
from app.modules.assessments.service import active_question, student_result


def _privileged() -> dict[str, Any]:
    return {
        "id": new_id(),
        "question_type": "mcq_single",
        "prompt": "Pick one",
        "marks": "2",
        "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
        "answer_key": {"correct_option_ids": ["a"]},
        "explanation": "PRIVATE EXPLANATION",
        "awarded_marks": "2",
        "is_correct": True,
    }


def test_active_projection_drops_privileged_fields_without_rls() -> None:
    raw = _privileged()
    projected = active_question(raw).model_dump_json()
    for field in ("answer_key", "explanation", "awarded_marks", "is_correct", "PRIVATE"):
        assert field not in projected
    with pytest.raises(ValidationError):
        ActiveQuestion.model_validate(raw)


@pytest.mark.parametrize("nested", ["options", "saved_answer"])
def test_nested_active_fields_cannot_carry_secrets(nested: str) -> None:
    raw = _privileged()
    raw = {
        k: v
        for k, v in raw.items()
        if k not in ("answer_key", "explanation", "awarded_marks", "is_correct")
    }
    if nested == "options":
        raw["options"][0]["is_correct"] = True
    else:
        raw["saved_answer"] = {"option_ids": ["a"], "explanation": "PRIVATE"}
    with pytest.raises(ValidationError):
        ActiveQuestion.model_validate(raw)


@pytest.mark.parametrize("mode", ["score_only", "correct_answers", "explanations"])
@pytest.mark.parametrize("timing", ["immediately", "after_attempts_exhausted"])
@pytest.mark.parametrize(("used", "active"), [(1, False), (2, True), (2, False)])
def test_result_mode_and_timing_enforced_without_rls(
    mode: RevealMode, timing: RevealTiming, used: int, active: bool
) -> None:
    context = ResultContext(
        state="submitted",
        configured_reveal_mode=mode,
        reveal_timing=timing,
        attempts_allowed=2,
        attempts_used=used,
        has_active_attempt=active,
    )
    solution = {
        "question_id": new_id(),
        "answer_key": {"correct_option_ids": ["a"]},
        "explanation": "PRIVATE EXPLANATION",
    }
    common: dict[str, Any] = {
        "attempt_id": new_id(),
        "context": context,
        "score": Decimal("1"),
        "max_marks": Decimal("2"),
        "pass_marks": Decimal("1"),
    }
    result = student_result(**common, solutions=[solution])
    body = result.model_dump()
    permitted = mode != "score_only" and (timing == "immediately" or (used == 2 and not active))
    assert ("solutions" in body) == permitted
    assert ("PRIVATE EXPLANATION" in result.model_dump_json()) == (
        permitted and mode == "explanations"
    )
    if not permitted:
        for model in (AnswersResult, ExplanationsResult):
            with pytest.raises(ValidationError):
                model.model_validate({**common, "passed": True, "solutions": [solution]})


def test_unsubmitted_context_and_explanation_mode_bypass_refused() -> None:
    with pytest.raises(ValidationError):
        ResultContext.model_validate(
            {
                "state": "in_progress",
                "configured_reveal_mode": "explanations",
                "reveal_timing": "immediately",
                "attempts_allowed": 2,
                "attempts_used": 1,
                "has_active_attempt": True,
            }
        )
    context = ResultContext(
        state="submitted",
        configured_reveal_mode="correct_answers",
        reveal_timing="immediately",
        attempts_allowed=2,
        attempts_used=1,
        has_active_attempt=False,
    )
    with pytest.raises(ValidationError):
        ExplanationsResult.model_validate(
            {
                "attempt_id": new_id(),
                "context": context,
                "score": "1",
                "max_marks": "2",
                "pass_marks": "1",
                "passed": True,
                "solutions": [],
            }
        )
