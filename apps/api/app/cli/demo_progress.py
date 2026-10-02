"""Seed-only shortcuts for demo progress (used by `app.cli.seed_demo`, nothing else).

Real watching can't be produced quickly: a heartbeat earns at most the real time since the last
one. So the demo seed writes a fully watched bitmap for a student's video lesson, then asks the
enrollments module to complete it with its normal rule (`complete_watched_video`).

**Guarded:** `mark_video_watched` raises `SeedOnlyError` unless it runs inside `seed_context()`
(entered by the demo seed's entry point) or ENVIRONMENT is local. `tests/test_cli_boundaries.py`
fails if anything under app/api or app/modules imports this module.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.courses import service as courses
from app.modules.enrollments import service as enrollments
from app.modules.enrollments.models import Enrollment, LessonProgress, LessonProgressStatus

SEGMENT_SECONDS = 5
_IN_SEED: ContextVar[bool] = ContextVar("demo_seed", default=False)


class SeedOnlyError(RuntimeError):
    """A seed-only helper was called outside the seed (and not in local development)."""


@contextmanager
def seed_context() -> Iterator[None]:
    """Entered by the demo seed's entry point: allows the seed-only helpers in this context."""
    token = _IN_SEED.set(True)
    try:
        yield
    finally:
        _IN_SEED.reset(token)


def _require_seed() -> None:
    if not _IN_SEED.get() and get_settings().environment != "local":
        msg = "mark_video_watched is for the demo seed only (app.cli.seed_demo)."
        raise SeedOnlyError(msg)


def watched_bitmap(duration_seconds: int) -> bytes:
    """Every 5-second segment set, most significant bit first (as the heartbeat buffer stores)."""
    segments = max(1, -(-duration_seconds // SEGMENT_SECONDS))
    full, rest = divmod(segments, 8)
    tail = bytes([(0xFF << (8 - rest)) & 0xFF]) if rest else b""
    return bytes([0xFF] * full) + tail


async def mark_video_watched(session: AsyncSession, enrollment_id: UUID, lesson_id: UUID) -> int:
    """Record the whole video as watched for this enrollment, then complete the lesson with the
    enrollments module's normal rule. Returns the course progress percentage."""
    _require_seed()
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None:
        msg = f"No enrollment {enrollment_id}"
        raise LookupError(msg)
    version = await courses.resolve_version(session, enrollment.course_id, enrollment.major_version)
    lesson = await courses.version_lesson(session, version.id, lesson_id) if version else None
    if lesson is None or lesson.video_asset_id is None:
        msg = f"Lesson {lesson_id} isn't a video in the student's version"
        raise LookupError(msg)
    duration = lesson.video_duration_seconds or 0
    values = {
        "watched_segments": watched_bitmap(duration),
        "watched_ratio": 1,
        "video_asset_id": lesson.video_asset_id,
        "video_position_seconds": duration,
    }
    await session.execute(
        pg_insert(LessonProgress)
        .values(
            enrollment_id=enrollment.id,
            lesson_id=lesson_id,
            organization_id=enrollment.organization_id,
            user_id=enrollment.user_id,
            status=LessonProgressStatus.IN_PROGRESS,
            **values,
        )
        .on_conflict_do_update(
            index_elements=[LessonProgress.enrollment_id, LessonProgress.lesson_id], set_=values
        )
    )
    percent = await enrollments.complete_watched_video(session, enrollment_id, lesson_id)
    assert percent is not None  # noqa: S101 - just recorded as fully watched
    return percent
