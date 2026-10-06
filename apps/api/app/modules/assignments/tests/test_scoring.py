"""UTC begun-day boundaries, exact earned-mark penalties and rubric validation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.core.errors import UnprocessableError
from app.modules.assignments.schemas import GradeBody, PublishedAssignment
from app.modules.assignments.scoring import grade_values, lateness

DUE = datetime(2026, 10, 6, 0, tzinfo=UTC)


def definition(**fields: object) -> PublishedAssignment:
    return PublishedAssignment.model_validate(
        {
            "assignment_id": str(UUID(int=1)),
            "title": "HW",
            "instructions_html": "",
            "due_at": DUE,
            "max_marks": 10,
            "submission_kinds": ["text"],
            "late_policy": {"mode": "penalty", "percent_per_day": "10"},
            **fields,
        }
    )


@pytest.mark.parametrize(
    ("seconds", "days", "percent"),
    [
        (-1, 0, 0),
        (0, 0, 0),
        (1, 1, 10),
        (86400, 1, 10),
        (86401, 2, 20),
        (864000, 10, 100),
        (864001, 11, 100),
    ],
)
def test_begun_utc_day_and_penalty_cap(seconds: int, days: int, percent: int) -> None:
    assignment = definition()
    late = lateness(assignment, DUE + timedelta(seconds=seconds))
    assert (late.late_days, late.penalty_percent, late.is_late) == (
        days,
        Decimal(percent),
        days > 0,
    )
    values = grade_values(assignment, late, GradeBody(score=Decimal(8)))
    assert values["penalty_marks"] == Decimal(8) * percent / 100
    assert values["score"] == Decimal(8) * (100 - percent) / 100


@pytest.mark.parametrize("mode", ["accept", "reject"])
def test_accept_reject_and_no_deadline(mode: str) -> None:
    a = definition(late_policy={"mode": mode})
    late = lateness(a, DUE + timedelta(seconds=1))
    assert late.is_late
    assert late.late_days == 1
    assert late.penalty_percent == 0
    assert late.closed == (mode == "reject")
    a.due_at = None
    late = lateness(a, DUE + timedelta(days=50))
    assert not late.is_late
    assert not late.closed
    assert late.late_days == 0


def test_penalty_half_up_and_regrade_uses_earned_marks() -> None:
    a = definition(late_policy={"mode": "penalty", "percent_per_day": "12.5"})
    late = lateness(a, DUE + timedelta(seconds=1))
    first = grade_values(a, late, GradeBody(score=Decimal("0.04")))
    assert first["penalty_marks"] == Decimal("0.01")
    assert first["score"] == Decimal("0.03")
    corrected = grade_values(a, late, GradeBody(score=Decimal("8")))
    assert corrected["score"] == Decimal("7.00")


@pytest.mark.parametrize(
    "scores",
    [
        None,
        [],
        [{"criterion_id": "a", "score": "1"}],
        [{"criterion_id": "a", "score": "1"}, {"criterion_id": "a", "score": "2"}],
        [{"criterion_id": "a", "score": "1"}, {"criterion_id": "unknown", "score": "2"}],
        [{"criterion_id": "a", "score": "5.01"}, {"criterion_id": "b", "score": "0"}],
    ],
)
def test_rubric_cannot_be_bypassed(scores: list[dict[str, str]] | None) -> None:
    a = definition(
        rubric={
            "criteria": [
                {"id": "a", "label": "A", "max_marks": "5"},
                {"id": "b", "label": "B", "max_marks": "5"},
            ]
        }
    )
    body = GradeBody.model_validate({"score": "10", "criterion_scores": scores or None})
    with pytest.raises(UnprocessableError):
        grade_values(a, None, body)


def test_rubric_total_is_derived_and_not_the_client_total() -> None:
    a = definition(
        rubric={
            "criteria": [
                {"id": "a", "label": "A", "max_marks": "5"},
                {"id": "b", "label": "B", "max_marks": "5"},
            ]
        }
    )
    values = grade_values(
        a,
        None,
        GradeBody.model_validate(
            {
                "score": "10",
                "criterion_scores": [
                    {"criterion_id": "b", "score": "3"},
                    {"criterion_id": "a", "score": "4"},
                ],
            }
        ),
    )
    assert values["raw_score"] == Decimal("7.00")
    assert values["score"] == Decimal("7.00")
    assert values["rubric_breakdown"] == [
        {"criterion_id": "b", "score": "3.00"},
        {"criterion_id": "a", "score": "4.00"},
    ]
