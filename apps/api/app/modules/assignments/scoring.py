"""Frozen rubric and earned-mark late penalties, using exact decimal arithmetic."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.core.errors import UnprocessableError
from app.modules.assignments.schemas import GradeBody, LateData, PublishedAssignment

CENT = Decimal("0.01")


def lateness(assignment: PublishedAssignment, accepted_at: datetime) -> LateData:
    due = assignment.due_at
    late = due is not None and accepted_at.astimezone(UTC) > due.astimezone(UTC)
    days = 0
    if late and due is not None:
        elapsed = accepted_at - due
        days = (elapsed // timedelta(days=1)) + (elapsed % timedelta(days=1) != timedelta(0))
    policy = assignment.late_policy
    percent = (
        min(Decimal(100), days * (policy.percent_per_day or Decimal(0)))
        if policy.mode == "penalty"
        else Decimal(0)
    )
    return LateData(
        due_at=due,
        policy=policy,
        is_late=late,
        late_days=days,
        penalty_percent=percent,
        closed=late and policy.mode == "reject",
    )


def grade_values(
    assignment: PublishedAssignment, late: LateData | None, body: GradeBody
) -> dict[str, object]:
    breakdown = None
    if assignment.rubric:
        if body.criterion_scores is None:
            raise UnprocessableError("Score every rubric criterion.", code="rubric_scores_required")
        scores = {s.criterion_id: s.score for s in body.criterion_scores}
        criteria = {c.id: c for c in assignment.rubric.criteria}
        if len(scores) != len(body.criterion_scores) or scores.keys() != criteria.keys():
            raise UnprocessableError(
                "Score each rubric criterion exactly once.", code="invalid_rubric_scores"
            )
        if any(scores[key] > c.max_marks for key, c in criteria.items()):
            raise UnprocessableError(
                "Criterion score exceeds its maximum.", code="score_out_of_range"
            )
        raw = sum(scores.values(), Decimal(0))
        breakdown = [
            {"criterion_id": s.criterion_id, "score": f"{s.score:.2f}"}
            for s in body.criterion_scores
        ]
    else:
        if body.score is None or body.criterion_scores is not None:
            raise UnprocessableError(
                "Provide a total score for this assignment.", code="score_required"
            )
        raw = body.score
    raw = raw.quantize(CENT, rounding=ROUND_HALF_UP)
    if raw > assignment.max_marks:
        raise UnprocessableError(
            f"The score can't be more than {assignment.max_marks}.",
            code="score_out_of_range",
            details={"max_marks": assignment.max_marks},
        )
    percent = late.penalty_percent if late else Decimal(0)
    penalty = (raw * percent / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return {
        "raw_score": raw,
        "penalty_percent": percent,
        "penalty_marks": penalty,
        "score": max(Decimal(0), raw - penalty),
        "rubric_breakdown": breakdown,
    }
