"""Enrollments and progress API models."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class EnrollmentOut(BaseModel):
    id: UUID
    course_id: UUID
    course_title: str
    major_version: int
    version: str | None = Field(description='The version the student sees, e.g. "1.2"')
    status: Literal["active", "revoked"]
    progress_percent: int
    last_lesson_id: UUID | None
    last_accessed_at: datetime | None
    completed_at: datetime | None
    enrolled_at: datetime


class LessonProgressOut(BaseModel):
    lesson_id: UUID
    status: Literal["not_started", "in_progress", "completed"]
    video_position_seconds: int | None
    watched_ratio: Decimal | None
    completed_at: datetime | None


class EnrollmentVersion(BaseModel):
    id: UUID
    major: int
    minor: int
    version: str
    title: str


class EnrollmentDetail(BaseModel):
    """What the course player needs: the pinned version's outline and the student's progress."""

    enrollment: EnrollmentOut
    version: EnrollmentVersion
    outline: dict[str, Any] = Field(description="The version snapshot (modules and lessons)")
    progress: list[LessonProgressOut]


class LessonCompletionOut(BaseModel):
    enrollment: EnrollmentOut
    lesson: LessonProgressOut


class UpgradeRequest(BaseModel):
    to_major: int = Field(ge=2)
    batch_ids: Annotated[list[UUID], Field(max_length=100)] = Field(
        default_factory=list,
        description="Only students in these batches (default: the whole organization)",
    )

    @field_validator("batch_ids")
    @classmethod
    def _unique(cls, ids: list[UUID]) -> list[UUID]:
        if len(set(ids)) != len(ids):
            msg = "batch_ids must be unique"
            raise ValueError(msg)
        return ids


class UpgradeAccepted(BaseModel):
    course_id: UUID
    to_major: int
    batch_ids: list[UUID]
    status: Literal["queued"] = "queued"
