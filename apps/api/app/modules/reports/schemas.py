from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

LessonCell = Literal["completed", "in_progress", "not_started", "not_in_version"]


class ProgressLesson(BaseModel):
    """A column: a lesson of the course's latest version, in outline order."""

    id: UUID
    title: str
    lesson_type: str
    module_title: str


class AssignmentCell(BaseModel):
    status: Literal["submitted", "graded"]
    score: Decimal | None
    max_marks: int | None


class StudentRef(BaseModel):
    id: UUID
    full_name: str
    email: str


class StudentProgress(BaseModel):
    student: StudentRef
    enrollment_id: UUID | None
    enrollment_status: Literal["active", "revoked", "not_enrolled"]
    version: str | None = Field(description='The version the student sees, e.g. "1.2"')
    progress_percent: int
    last_activity_at: datetime | None
    completed_at: datetime | None
    lessons: dict[UUID, LessonCell] = Field(description="Per lesson column")
    assignments: dict[UUID, AssignmentCell] = Field(
        description="Per assignment lesson the student submitted"
    )


class CourseProgressPage(BaseModel):
    course_id: UUID
    course_title: str
    batch_id: UUID
    version: str | None = Field(description="The latest version the columns come from")
    lessons: list[ProgressLesson]
    items: list[StudentProgress]
    next_cursor: str | None


class BatchCourseSummary(BaseModel):
    course_id: UUID
    title: str
    version: str | None
    enrolled: int = Field(description="The batch's students actively enrolled")
    completed: int
    average_percent: int
