"""Names and enqueue helpers for the enrollments module's background jobs.

Kept free of heavy imports so other modules (courses) can enqueue enrollment work without importing
the enrollments service or Celery. Jobs are enqueued only after the transaction commits.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import JobQueue
from app.db.session import run_after_commit

RECONCILE_COURSE_ORG = "enrollments.reconcile_course_org"
UPGRADE_ENROLLMENTS = "enrollments.upgrade_enrollments"


def enqueue_reconcile(
    session: AsyncSession, jobs: JobQueue, course_id: UUID, organization_id: UUID
) -> None:
    """Bring a course's enrollments in one org in line with its batch assignments."""
    run_after_commit(
        session, lambda: jobs.send(RECONCILE_COURSE_ORG, str(course_id), str(organization_id))
    )


def enqueue_upgrade(
    session: AsyncSession,
    jobs: JobQueue,
    *,
    course_id: UUID,
    organization_id: UUID,
    user_id: UUID,
    to_major: int,
    batch_ids: Sequence[UUID],
) -> None:
    args = (
        str(course_id),
        str(organization_id),
        str(user_id),
        str(to_major),
        ",".join(str(b) for b in batch_ids),
    )
    run_after_commit(session, lambda: jobs.send(UPGRADE_ENROLLMENTS, *args))
