"""Explicit student projections shared by future authoring/runtime endpoints.

SQL reveal and response validation independently enforce answer secrecy. Only
this module's repository touches assessment tables; other modules call services.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.assessments.schemas import (
    ActiveQuestion,
    AnswersResult,
    ExplanationsResult,
    ResultContext,
    SavedAnswer,
    ScoreResult,
)


def active_question(
    question: Mapping[str, Any], *, saved_answer: SavedAnswer | None = None
) -> ActiveQuestion:
    """Allow-list fields, including typed nested options; discard privileged source fields."""
    return ActiveQuestion.model_validate(
        {
            "id": question["id"],
            "question_type": question["question_type"],
            "prompt": question["prompt"],
            "options": question["options"],
            "marks": question["marks"],
            "skill_ids": question.get("skill_ids", []),
            "saved_answer": saved_answer,
        }
    )


def student_result(
    *,
    attempt_id: UUID,
    context: ResultContext,
    score: Decimal,
    max_marks: Decimal,
    pass_marks: Decimal,
    solutions: Sequence[Mapping[str, Any]],
) -> ScoreResult | AnswersResult | ExplanationsResult:
    """Privileged fixtures remain safe even without SQL filtering the solution input."""
    common = {
        "attempt_id": attempt_id,
        "context": context,
        "score": score,
        "max_marks": max_marks,
        "pass_marks": pass_marks,
        "passed": score >= pass_marks,
    }
    if not context.solutions_allowed():
        return ScoreResult.model_validate(common)
    safe = [
        {"question_id": row["question_id"], "answer_key": row["answer_key"]} for row in solutions
    ]
    if context.configured_reveal_mode == "correct_answers":
        return AnswersResult.model_validate({**common, "solutions": safe})
    explained = [
        {**row, "explanation": original["explanation"]}
        for row, original in zip(safe, solutions, strict=True)
    ]
    return ExplanationsResult.model_validate({**common, "solutions": explained})
