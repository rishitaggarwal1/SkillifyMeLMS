"""Assignments API models."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, field_validator

from app.modules.media.schemas import FileName, FileUploadOut, SubmissionContentType

SubmissionKindName = Literal["file", "text"]
SubmissionStatusName = Literal["submitted", "graded"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

MAX_TEXT_CHARS = 20_000
MAX_FEEDBACK_CHARS = 10_000
MAX_INSTRUCTIONS_BYTES = 100_000

__all__ = ["FileUploadOut"]


# ============================================================================ authoring


class AssignmentUpsert(BaseModel):
    title: Title
    instructions: dict[str, Any] | None = Field(
        default=None,
        description="Tiptap JSON document with the notes allow-list (see courses/notes.py), "
        "without images.",
    )
    due_at: AwareDatetime | None = Field(
        default=None,
        description="Shown to students. Late submissions are accepted (no policy yet).",
    )
    max_marks: int = Field(ge=1, le=1000)
    submission_kinds: list[SubmissionKindName] = Field(min_length=1, max_length=2)

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


# ============================================================================ published


class PublishedAssignment(BaseModel):
    """An assignment as the student's course version published it."""

    assignment_id: UUID
    title: str
    instructions_html: str
    due_at: datetime | None
    max_marks: int
    submission_kinds: list[SubmissionKindName]


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


class SubmissionOut(BaseModel):
    id: UUID
    kind: SubmissionKindName
    text_body: str | None
    file: SubmissionFileOut | None
    status: SubmissionStatusName
    revision: int = Field(description="Send it as If-Match to resubmit (students) or grade")
    submitted_at: datetime
    grade: GradeOut | None


class StudentAssignmentOut(BaseModel):
    assignment: PublishedAssignment
    submission: SubmissionOut | None


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


class GraderSubmissionDetail(BaseModel):
    id: UUID
    course_id: UUID
    lesson_id: UUID
    student: StudentSummary
    assignment: PublishedAssignment
    submission: SubmissionOut


class GradeBody(BaseModel):
    score: Decimal = Field(ge=0, max_digits=6, decimal_places=2)
    feedback: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=MAX_FEEDBACK_CHARS)
    ] = ""
