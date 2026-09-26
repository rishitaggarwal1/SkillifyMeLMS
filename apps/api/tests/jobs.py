"""Run background jobs recorded by `RecordingJobQueue` inline, the way the Celery worker would."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.enrollments import service as enrollments
from app.modules.enrollments.jobs import RECONCILE_COURSE_ORG, UPGRADE_ENROLLMENTS
from app.modules.enrollments.tasks import parse_upgrade_args, run_reconcile_course_org
from tests.fakes import RecordingJobQueue


async def run_jobs(queue: RecordingJobQueue, sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """Run every recorded job (including ones enqueued while running); returns how many ran."""
    ran = 0
    while jobs := queue.take():
        for task, args in jobs:
            if task == RECONCILE_COURSE_ORG:
                await run_reconcile_course_org(sessionmaker, UUID(args[0]), UUID(args[1]))
            elif task == UPGRADE_ENROLLMENTS:
                await enrollments.run_upgrade(sessionmaker, **parse_upgrade_args(*args))  # type: ignore[arg-type]
            else:
                msg = f"no inline runner for job {task}"
                raise AssertionError(msg)
            ran += 1
    return ran
