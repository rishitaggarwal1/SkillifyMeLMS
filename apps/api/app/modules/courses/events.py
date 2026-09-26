"""Domain events published by the courses module (through the outbox; see docs/events.md)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.outbox import add_outbox_event
from app.modules.courses.models import CourseVersion

COURSE_AGGREGATE = "course"
COURSE_PUBLISHED = "course_published"
SCHEMA_VERSION = 1


def course_published(
    session: AsyncSession, version: CourseVersion, *, is_public_catalog: bool
) -> None:
    add_outbox_event(
        session,
        aggregate_type=COURSE_AGGREGATE,
        aggregate_id=version.course_id,
        event_type=COURSE_PUBLISHED,
        organization_id=version.organization_id,
        payload={
            "course_id": str(version.course_id),
            "version_id": str(version.id),
            "major": version.major,
            "minor": version.minor,
            "release_type": version.release_type,
            "is_public_catalog": is_public_catalog,
            "published_by": str(version.published_by) if version.published_by else None,
        },
        headers={"version": SCHEMA_VERSION},
    )
