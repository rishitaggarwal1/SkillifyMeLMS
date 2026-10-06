"""Assignments API models."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.modules.media.schemas import FileName, FileUploadOut, SubmissionContentType

SubmissionKindName = Literal["file", "text"]
SubmissionStatusName = Literal["submitted", "graded"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

MAX_TEXT_CHARS = 20_000
MAX_FEEDBACK_CHARS = 10_000
MAX_INSTRUCTIONS_BYTES = 100_000

Marks = Annotated[Decimal, Field(ge=0, max_digits=7, decimal_places=2)]


class RubricCriterion(BaseModel):
    id: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
    label: Title
    description: str = Field(default="", max_length=2000)
    max_marks: Annotated[Decimal, Field(gt=0, le=1000, decimal_places=2)]


class Rubric(BaseModel):
    criteria: list[RubricCriterion] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_ids(self) -> "Rubric":
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError("Rubric criterion ids must be unique")
        return self


class LatePolicy(BaseModel):
    mode: Literal["accept", "reject", "penalty"] = "accept"
    percent_per_day: Annotated[Decimal, Field(gt=0, le=100, decimal_places=2)] | None = None

    @model_validator(mode="after")
    def rate(self) -> "LatePolicy":
        if (self.mode == "penalty") != (self.percent_per_day is not None):
            raise ValueError("Only penalty mode requires percent_per_day")
        return self


class LateData(BaseModel):
    due_at: datetime | None
    policy: LatePolicy
    is_late: bool
    late_days: int
    penalty_percent: Marks
    closed: bool = False


class CriterionScore(BaseModel):
    criterion_id: str = Field(min_length=1, max_length=64)
    score: Marks


__all__ = ["FileUploadOut"]


# ============================================================================ authoring


class AssignmentUpsert(BaseModel):
    title: Title
    instructions: dict[str, Any] | None = Field(
        default=None,
        description="Tiptap JSON document with the notes allow-list (see courses/notes.py), "
        "with confirmed same-owner image file IDs.",
    )
    due_at: AwareDatetime | None = Field(
        default=None,
        description="UTC due time, enforced against the configured late policy.",
    )
    max_marks: int = Field(ge=1, le=1000)
    submission_kinds: list[SubmissionKindName] = Field(min_length=1, max_length=2)
    rubric: Rubric | None = None
    late_policy: LatePolicy | None = None

    @field_validator("submission_kinds")
    @classmethod
    def _unique(cls, kinds: list[SubmissionKindName]) -> list[SubmissionKindName]:
        if len(set(kinds)) != len(kinds):
            msg = "submission_kinds must be unique"
            raise ValueError(msg)
        return sorted(kinds)


class AssignmentDraftOut(BaseModel):
    """The editable definition (owner-org editors). Students see the published copy."""

    id: UUID
    course_id: UUID
    lesson_id: UUID
    title: str
    instructions: dict[str, Any] | None
    due_at: datetime | None
    max_marks: int
    submission_kinds: list[SubmissionKindName]
    course_revision: int = Field(description="Send it as If-Match on the next outline edit")
    updated_at: datetime
    rubric: Rubric | None = None
    late_policy: LatePolicy | None = None


# ============================================================================ published


class PublishedAssignment(BaseModel):
    """An assignment as the student's course version published it."""

    assignment_id: UUID
    title: str
    instructions_html: str
    due_at: datetime | None
    max_marks: int
    submission_kinds: list[SubmissionKindName]
    rubric: Rubric | None = None
    late_policy: LatePolicy = Field(default_factory=LatePolicy)
    image_file_ids: list[UUID] = Field(default_factory=list)


class SubmissionFileOut(BaseModel):
    id: UUID
    file_name: str
    content_type: str
    url: str | None = Field(description="Short-lived signed download URL (null until confirmed)")
    expires_at: datetime | None


class GradeOut(BaseModel):
    score: Decimal
    max_marks: int
    feedback: str
    graded_at: datetime
    graded_by: UUID | None
    id: UUID | None = None
    attempt_id: UUID | None = None
    grade_sequence: int | None = None
    rubric_breakdown: list[CriterionScore] | None = None
    raw_score: Decimal | None = None
    penalty_percent: Decimal | None = None
    penalty_marks: Decimal | None = None


class SubmissionOut(BaseModel):
    id: UUID
    kind: SubmissionKindName
    text_body: str | None
    file: SubmissionFileOut | None
    status: SubmissionStatusName
    revision: int = Field(description="Send it as If-Match to resubmit (students) or grade")
    submitted_at: datetime
    grade: GradeOut | None
    active_attempt_id: UUID | None = None
    attempt_number: int | None = None
    late: LateData | None = None


class StudentAssignmentOut(BaseModel):
    assignment: PublishedAssignment
    submission: SubmissionOut | None
    server_time: datetime | None = None
    late: LateData | None = None


class SubmitText(BaseModel):
    kind: Literal["text"]
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_CHARS)
    ]


class SubmitFile(BaseModel):
    kind: Literal["file"]
    file_id: UUID = Field(description="An upload from POST .../submission-upload")


class SubmitBody(BaseModel):
    submission: SubmitText | SubmitFile = Field(discriminator="kind")


class SubmissionUploadCreate(BaseModel):
    file_name: FileName
    content_type: SubmissionContentType


# ============================================================================ grading


class StudentSummary(BaseModel):
    id: UUID
    full_name: str
    email: str


class GraderSubmissionRow(BaseModel):
    id: UUID
    student: StudentSummary
    enrollment_id: UUID
    kind: SubmissionKindName
    status: SubmissionStatusName
    revision: int
    submitted_at: datetime
    score: Decimal | None
    max_marks: int | None
    active_attempt_id: UUID | None = None


class CrossCourseSubmissionRow(GraderSubmissionRow):
    course_id: UUID
    course_title: str
    assignment_title: str


class GraderSubmissionDetail(BaseModel):
    id: UUID
    course_id: UUID
    lesson_id: UUID
    student: StudentSummary
    assignment: PublishedAssignment
    submission: SubmissionOut


class GradeBody(BaseModel):
    score: Decimal | None = Field(default=None, ge=0, max_digits=6, decimal_places=2)
    criterion_scores: list[CriterionScore] | None = Field(default=None, min_length=1, max_length=50)
    attempt_id: UUID | None = None
    feedback: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=MAX_FEEDBACK_CHARS)
    ] = ""


class SubmissionAttemptOut(BaseModel):
    id: UUID
    submission_id: UUID
    attempt_number: int
    version_id: UUID
    submitted_at: datetime
    is_active: bool
    kind: SubmissionKindName
    text_body: str | None
    file: SubmissionFileOut | None
    assignment: PublishedAssignment
    late: LateData | None
    grade: GradeOut | None
    image_urls: dict[UUID, str] = Field(default_factory=dict)
    image_urls_expires_at: datetime | None = None


class AssignmentPreviewOut(BaseModel):
    html: str
    image_urls: dict[UUID, str]
    expires_at: datetime | None
