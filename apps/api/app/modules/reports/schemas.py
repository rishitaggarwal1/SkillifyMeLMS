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


class AdminOverview(BaseModel):
    has_batch: bool
    has_students: bool
    has_assignment: bool
    pending_invitations: int
    running_imports: int
    failed_imports: int


class DashboardBatch(BaseModel):
    id: UUID
    name: str
    completion_percent: int | None = Field(
        description="Mean progress across active, entitled enrollments; null if none"
    )
    last_activity_at: datetime | None
    enrolled: int


class TeachOverview(BaseModel):
    has_course: bool
    has_lesson: bool
    has_publication: bool
    ungraded_count: int
    oldest_ungraded_at: datetime | None
    assignments_due_soon: int
    inactive_students: int
    quiz_passes: int
    quiz_failures: int


class DueAssignment(BaseModel):
    enrollment_id: UUID
    course_id: UUID
    lesson_id: UUID
    course_title: str
    title: str
    due_at: datetime


class LearningResult(BaseModel):
    """Score-only submitted outcomes; no response can carry answer-key material."""

    id: UUID
    kind: Literal["assignment", "quiz"]
    enrollment_id: UUID
    course_id: UUID
    lesson_id: UUID
    course_title: str
    title: str
    occurred_at: datetime
    score: Decimal
    max_marks: Decimal
    passed: bool | None
