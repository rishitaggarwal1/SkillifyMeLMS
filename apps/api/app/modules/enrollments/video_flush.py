"""Batch persistence of video heartbeats. All database reads/writes are batched.

A transaction advisory lock serializes flush snapshots across workers. Redis acknowledgement
happens after commit; buffer_revision makes a crash between commit and acknowledgement harmless.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.outbox import add_outbox_event
from app.db.tenancy import set_tenant_context, system_transaction
from app.modules.courses import service as courses
from app.modules.enrollments import events
from app.modules.enrollments.models import Enrollment, LessonProgress
from app.modules.enrollments.progress import course_percent, video_watched
from app.modules.enrollments.repository import EnrollmentRepository, LessonProgressRepository
from app.modules.enrollments.video_buffer import BufferedVideo, VideoBuffer

FLUSH_LOCK = 73210941


@dataclass(frozen=True, slots=True)
class FlushResult:
    sampled: int  # dirty entries read from Redis (a full batch means more may be waiting)
    changed: int  # lesson_progress rows written


async def flush_video_progress(
    sessionmaker: async_sessionmaker[AsyncSession], redis: Redis, *, limit: int = 500
) -> FlushResult:
    buffer = VideoBuffer(redis)
    async with system_transaction(sessionmaker) as session:
        locked = await session.scalar(select(func.pg_try_advisory_xact_lock(FLUSH_LOCK)))
        if not locked:  # another worker is flushing
            return FlushResult(sampled=0, changed=0)
        pending = await buffer.pending(limit)
        if not pending:
            return FlushResult(sampled=0, changed=0)
        by_org: dict[UUID, list[BufferedVideo]] = defaultdict(list)
        for entry in pending:
            by_org[UUID(entry.data["organization_id"])].append(entry)
        changed = 0
        for org, entries in by_org.items():
            await set_tenant_context(
                session, organization_id=org, user_id=None, is_platform_admin=True
            )
            changed += await _persist(session, entries)
            await session.flush()  # outbox INSERT uses this tenant's context
    await buffer.acknowledge(pending)
    return FlushResult(sampled=len(pending), changed=changed)


def _row(
    entry: BufferedVideo,
    enrollment: Enrollment,
    existing: LessonProgress | None,
    lesson: courses.VersionLessonRef,
) -> dict[str, Any]:
    completed = existing is not None and existing.status == "completed"
    ratio = Decimal(str(entry.ratio))
    completed = completed or video_watched(ratio, lesson.completion_threshold)
    return {
        "enrollment_id": enrollment.id,
        "lesson_id": lesson.lesson_id,
        "organization_id": enrollment.organization_id,
        "user_id": enrollment.user_id,
        "video_position_seconds": int(entry.data["position"]),
        "watched_segments": entry.bitmap,
        "watched_ratio": ratio,
        "video_asset_id": lesson.video_asset_id,
        "buffer_revision": UUID(entry.data["revision"]),
        "status": "completed" if completed else "in_progress",
        "completed_at": ((existing.completed_at if existing else None) or datetime.now(UTC))
        if completed
        else None,
        "updated_at": datetime.now(UTC),
    }


async def _persist(session: AsyncSession, pending: list[BufferedVideo]) -> int:
    repo = EnrollmentRepository(session)
    progress_repo = LessonProgressRepository(session)
    ids = list({UUID(p.data["enrollment_id"]) for p in pending})
    enrollments = {e.id: e for e in await repo.lock_many(ids) if e.status == "active"}
    versions = await courses.resolve_versions(
        session, [(e.course_id, e.major_version) for e in enrollments.values()]
    )
    lessons = await courses.version_lessons_many(session, [v.id for v in versions.values()])
    lesson_map = {
        (vid, lesson.lesson_id): lesson for vid, rows in lessons.items() for lesson in rows
    }
    progress = {(p.enrollment_id, p.lesson_id): p for p in await progress_repo.for_enrollments(ids)}
    rows: list[dict[str, Any]] = []
    visits: dict[UUID, tuple[UUID, UUID, datetime]] = {}
    completed: list[tuple[Enrollment, courses.VersionRef, courses.VersionLessonRef]] = []
    for entry in pending:
        eid, lid = UUID(entry.data["enrollment_id"]), UUID(entry.data["lesson_id"])
        enrollment = enrollments.get(eid)
        if enrollment is None:
            continue
        version = versions.get((enrollment.course_id, enrollment.major_version))
        lesson = lesson_map.get((version.id, lid)) if version else None
        if lesson is None or str(lesson.video_asset_id) != entry.data["asset_id"]:
            continue  # replaced video / upgraded major: old heartbeats cannot revive old progress
        old = progress.get((eid, lid))
        if old is not None and str(old.buffer_revision) == entry.data["revision"]:
            continue  # committed before a crash; Redis acknowledgement was lost
        row = _row(entry, enrollment, old, lesson)
        rows.append(row)
        if row["status"] == "completed" and (old is None or old.status != "completed"):
            assert version is not None  # noqa: S101
            completed.append((enrollment, version, lesson))
        at = datetime.fromisoformat(entry.data["at"])
        if eid not in visits or visits[eid][2] < at:
            visits[eid] = (eid, lid, at)
        add_outbox_event(
            session,
            aggregate_type="video_progress",
            aggregate_id=eid,
            event_type="video_progress",
            organization_id=enrollment.organization_id,
            payload={
                "enrollment_id": str(eid),
                "lesson_id": str(lid),
                "video_asset_id": entry.data["asset_id"],
                "position_seconds": row["video_position_seconds"],
                "watched_ratio": float(row["watched_ratio"]),
            },
            headers={"version": 1},
        )
    await progress_repo.upsert_videos(rows)
    await repo.touch_many(list(visits.values()))
    done = {key for key, p in progress.items() if p.status == "completed"}
    done.update((r["enrollment_id"], r["lesson_id"]) for r in rows if r["status"] == "completed")
    percentages = {}
    for eid in visits:
        enrollment = enrollments[eid]
        version = versions[enrollment.course_id, enrollment.major_version]
        required = [
            lesson.lesson_id for lesson in lessons[version.id] if lesson.counts_toward_progress
        ]
        percentages[eid] = course_percent(
            sum((eid, lid) in done for lid in required), len(required)
        )
    await repo.set_progress(percentages)
    for enrollment, version, lesson in completed:
        events.lesson_completed(
            session,
            enrollment_id=enrollment.id,
            organization_id=enrollment.organization_id,
            user_id=enrollment.user_id,
            course_id=enrollment.course_id,
            lesson_id=lesson.lesson_id,
            lesson_type="video",
            version_id=version.id,
            progress_percent=percentages[enrollment.id],
        )
    return len(rows)
