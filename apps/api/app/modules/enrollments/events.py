"""Domain events published by the enrollments module (through the outbox; see docs/events.md).
Keyed by enrollment id, so one student's events for one course stay in order."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.outbox import add_outbox_event

ENROLLMENT_AGGREGATE = "enrollment"
ENROLLMENT_CREATED = "enrollment_created"
LESSON_COMPLETED = "lesson_completed"
ENROLLMENT_VERSION_CHANGED = "enrollment_version_changed"
SCHEMA_VERSION = 1


def _emit(
    session: AsyncSession,
    event_type: str,
    *,
    enrollment_id: UUID,
    organization_id: UUID,
    data: dict[str, Any],
) -> None:
    add_outbox_event(
        session,
        aggregate_type=ENROLLMENT_AGGREGATE,
        aggregate_id=enrollment_id,
        event_type=event_type,
        organization_id=organization_id,
        payload={"enrollment_id": str(enrollment_id), **data},
        headers={"version": SCHEMA_VERSION},
    )


def enrollment_created(
    session: AsyncSession,
    *,
    enrollment_id: UUID,
    organization_id: UUID,
    user_id: UUID,
    course_id: UUID,
    major_version: int,
    assignment_id: UUID | None,
) -> None:
    _emit(
        session, ENROLLMENT_CREATED, enrollment_id=enrollment_id, organization_id=organization_id,
        data={
            "user_id": str(user_id), "course_id": str(course_id), "major_version": major_version,
            "assignment_id": str(assignment_id) if assignment_id else None,
        },
    )  # fmt: skip


def lesson_completed(
    session: AsyncSession,
    *,
    enrollment_id: UUID,
    organization_id: UUID,
    user_id: UUID,
    course_id: UUID,
    lesson_id: UUID,
    lesson_type: str,
    version_id: UUID,
    progress_percent: int,
) -> None:
    _emit(
        session, LESSON_COMPLETED, enrollment_id=enrollment_id, organization_id=organization_id,
        data={
            "user_id": str(user_id), "course_id": str(course_id), "lesson_id": str(lesson_id),
            "lesson_type": lesson_type, "version_id": str(version_id),
            "progress_percent": progress_percent,
        },
    )  # fmt: skip


def enrollment_version_changed(
    session: AsyncSession,
    *,
    enrollment_id: UUID,
    organization_id: UUID,
    user_id: UUID,
    course_id: UUID,
    from_major: int,
    to_major: int,
    progress_percent: int,
    actor_user_id: UUID,
) -> None:
    _emit(
        session, ENROLLMENT_VERSION_CHANGED, enrollment_id=enrollment_id,
        organization_id=organization_id,
        data={
            "user_id": str(user_id), "course_id": str(course_id), "from_major": from_major,
            "to_major": to_major, "progress_percent": progress_percent,
            "actor_user_id": str(actor_user_id),
        },
    )  # fmt: skip
