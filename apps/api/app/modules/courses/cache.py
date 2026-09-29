"""Redis cache for published course versions (outline + lesson rows).

Versions are immutable, so each one is cached under its id for a long time:

    course:v:{version_id}                 -> {meta, snapshot, lessons}   (7 days)
    course:{course_id}:major:{n}:latest   -> version id                  (5 minutes)

The pointer is what changes: publishing a minor or a major moves it. It is deleted after the publish
transaction commits (`invalidate_pointer`), and its short TTL bounds any race with a reader that
resolved the old version just before the commit.

**Authorization is never cached.** A hit is returned only if the caller could read the version
through RLS right now: one query evaluates the same expression as the `course_versions` SELECT
policy (owner-org editor, platform admin, or `app.course_readable`) for every course involved, so
removing an assignment revokes access immediately, as it does without the cache.
"""

import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import Boolean, Text, Uuid, and_, cast, column, func, or_, select, values
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.courses.models import CourseVersion, CourseVersionLesson, LessonType
from app.modules.courses.repository import VersionRepository

VERSION_TTL = 7 * 86400
POINTER_TTL = 300
EDITOR_ROLES = ["instructor", "org_admin"]


def version_key(version_id: UUID) -> str:
    return f"course:v:{version_id}"


def pointer_key(course_id: UUID, major: int) -> str:
    return f"course:{course_id}:major:{major}:latest"


def _lesson_json(row: CourseVersionLesson) -> dict[str, Any]:
    return {
        "lesson_id": str(row.lesson_id),
        "lesson_type": row.lesson_type.value,
        "is_required": row.is_required,
        "completion_threshold": (
            None if row.completion_threshold is None else str(row.completion_threshold)
        ),
        "video_asset_id": str(row.video_asset_id) if row.video_asset_id else None,
        "video_duration_seconds": row.video_duration_seconds,
        "file_ids": [str(f) for f in row.file_ids or ()],
    }


def _entry(version: CourseVersion, lessons: Sequence[CourseVersionLesson]) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "course_id": str(version.course_id),
        "organization_id": str(version.organization_id),
        "major": version.major,
        "minor": version.minor,
        "title": version.title,
        "snapshot": version.snapshot,
        "lessons": [_lesson_json(row) for row in lessons],
    }


class CachedVersion:
    """A cached version: the fields services need, without an ORM row."""

    def __init__(self, entry: dict[str, Any]) -> None:
        self.id = UUID(entry["id"])
        self.course_id = UUID(entry["course_id"])
        self.organization_id = UUID(entry["organization_id"])
        self.major: int = entry["major"]
        self.minor: int = entry["minor"]
        self.title: str = entry["title"]
        self.snapshot: dict[str, Any] = entry["snapshot"]
        self.lessons: list[dict[str, Any]] = entry["lessons"]


def lesson_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Constructor arguments for `VersionLessonRef` from a cached lesson row."""
    return {
        "lesson_id": UUID(raw["lesson_id"]),
        "lesson_type": LessonType(raw["lesson_type"]),
        "is_required": raw["is_required"],
        "completion_threshold": (
            None if raw["completion_threshold"] is None else Decimal(raw["completion_threshold"])
        ),
        "video_asset_id": UUID(raw["video_asset_id"]) if raw["video_asset_id"] else None,
        "video_duration_seconds": raw["video_duration_seconds"],
        "file_ids": tuple(UUID(f) for f in raw["file_ids"]),
    }


class VersionCache:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    # ------------------------------------------------------------------ reads

    async def resolve(
        self, session: AsyncSession, pairs: Sequence[tuple[UUID, int]]
    ) -> dict[tuple[UUID, int], CachedVersion]:
        """The latest version of each (course, major), readable by the caller."""
        if not pairs:
            return {}
        pointers = await self.redis.mget([pointer_key(c, m) for c, m in pairs])
        version_ids: dict[tuple[UUID, int], UUID] = {}
        missing = []
        for pair, raw in zip(pairs, pointers, strict=True):
            if raw is None:
                missing.append(pair)
            else:
                version_ids[pair] = UUID(raw.decode() if isinstance(raw, bytes) else raw)
        if missing:
            # RLS filters these, so an unreadable course simply resolves to nothing.
            found = await VersionRepository(session).latest_for_majors(missing)
            pipe = self.redis.pipeline(transaction=False)
            for pair, version in found.items():
                version_ids[pair] = version.id
                pipe.set(pointer_key(*pair), str(version.id), ex=POINTER_TTL)
            await pipe.execute()
        versions = await self.versions(session, list(set(version_ids.values())))
        return {pair: versions[vid] for pair, vid in version_ids.items() if vid in versions}

    async def versions(
        self, session: AsyncSession, version_ids: Sequence[UUID]
    ) -> dict[UUID, CachedVersion]:
        """Cached versions by id, loaded on a miss, returned only if readable by the caller."""
        if not version_ids:
            return {}
        raws = await self.redis.mget([version_key(v) for v in version_ids])
        found: dict[UUID, CachedVersion] = {}
        missing = []
        for version_id, raw in zip(version_ids, raws, strict=True):
            if raw is None:
                missing.append(version_id)
            else:
                found[version_id] = CachedVersion(json.loads(raw))
        if missing:
            repo = VersionRepository(session)
            rows = await repo.get_many(missing)  # RLS-filtered
            lessons = await repo.lessons_many([v.id for v in rows])
            by_version: dict[UUID, list[CourseVersionLesson]] = {v.id: [] for v in rows}
            for lesson in lessons:
                by_version[lesson.version_id].append(lesson)
            pipe = self.redis.pipeline(transaction=False)
            for version in rows:
                entry = _entry(version, by_version[version.id])
                pipe.set(version_key(version.id), json.dumps(entry), ex=VERSION_TTL)
                found[version.id] = CachedVersion(entry)
            await pipe.execute()
        readable = await self._readable(session, found.values())
        return {vid: v for vid, v in found.items() if v.course_id in readable}

    async def _readable(self, session: AsyncSession, versions: Any) -> set[UUID]:
        """Courses the caller may read versions of: the `course_versions` SELECT policy."""
        pairs = {(v.course_id, v.organization_id) for v in versions}
        if not pairs:
            return set()
        courses = values(
            column("course_id", Uuid), column("organization_id", Uuid), name="courses"
        ).data(list(pairs))
        roles = cast(array(EDITOR_ROLES), ARRAY(Text))
        editor = or_(
            func.app.current_user_is_platform_admin(type_=Boolean),
            and_(
                courses.c.organization_id == func.app.current_org_id(type_=Uuid),
                func.app.current_user_has_role(
                    func.app.current_org_id(type_=Uuid), roles, type_=Boolean
                ),
            ),
        )
        readable = func.app.course_readable(courses.c.course_id, type_=Boolean)
        query = select(courses.c.course_id).where(or_(editor, readable))
        found: set[UUID] = set(await session.scalars(query))
        return found

    async def cached_latest(self, course_id: UUID, major: int) -> UUID | None:
        """The cached latest version id of (course, major), without authorization: only for
        telling whether something validated earlier is still current."""
        raw = await self.redis.get(pointer_key(course_id, major))
        return None if raw is None else UUID(raw.decode() if isinstance(raw, bytes) else raw)

    # ------------------------------------------------------------------ invalidation

    async def invalidate_pointer(self, course_id: UUID, major: int) -> None:
        await self.redis.delete(pointer_key(course_id, major))
