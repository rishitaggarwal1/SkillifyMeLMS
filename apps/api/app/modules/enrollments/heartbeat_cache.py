"""Short-lived cache of the heartbeat's authorization check, so a heartbeat costs no Postgres query.

A heartbeat arrives every 15 s from every playing student (~6.7k/s at 100k students). Validating
one takes the enrollment, the pinned version, the lesson and the progress baseline. The outcome of
that validation is cached for `TTL` seconds under the enrollment and lesson:

    hb:check:{enrollment_id}:{lesson_id}
        -> {user_id, organization_id, course_id, major, version_id, video_asset_id, duration}

A hit is used only by the same user in the same active organization, and only while the course's
version pointer (`course:{id}:major:{n}:latest`, deleted after every publish) still names the
version the check was made against. So a new release, such as a video replaced in a minor, is
seen on the next heartbeat. What a stale entry can still do within the TTL is bounded, because the
flush re-checks before writing progress:
- a revoked enrollment (assignment removed, then reconciled) is skipped (`status != active`);
- after a major upgrade, heartbeats keyed by the old version's asset are never merged into the new
  version's progress;
- playback and resume don't use this cache: they read through RLS, so revocation stays immediate.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis

from app.modules.courses import service as courses

TTL = 60


def check_key(enrollment_id: UUID, lesson_id: UUID) -> str:
    return f"hb:check:{enrollment_id}:{lesson_id}"


@dataclass(frozen=True, slots=True)
class HeartbeatCheck:
    user_id: UUID
    organization_id: UUID
    course_id: UUID
    major: int
    version_id: UUID
    video_asset_id: UUID
    duration_seconds: int


class HeartbeatCache:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def get(self, enrollment_id: UUID, lesson_id: UUID) -> HeartbeatCheck | None:
        """The cached check, if any and still current (its version is still the latest)."""
        raw = await self.redis.get(check_key(enrollment_id, lesson_id))
        if raw is None:
            return None
        data = json.loads(raw)
        check = HeartbeatCheck(
            user_id=UUID(data["user_id"]),
            organization_id=UUID(data["organization_id"]),
            course_id=UUID(data["course_id"]),
            major=int(data["major"]),
            version_id=UUID(data["version_id"]),
            video_asset_id=UUID(data["video_asset_id"]),
            duration_seconds=int(data["duration_seconds"]),
        )
        if not await courses.is_latest_version(
            self.redis, check.course_id, check.major, check.version_id
        ):
            return None  # published since (or pointer expired): validate against Postgres again
        return check

    async def put(self, enrollment_id: UUID, lesson_id: UUID, check: HeartbeatCheck) -> None:
        value = {
            "user_id": str(check.user_id),
            "organization_id": str(check.organization_id),
            "course_id": str(check.course_id),
            "major": check.major,
            "version_id": str(check.version_id),
            "video_asset_id": str(check.video_asset_id),
            "duration_seconds": check.duration_seconds,
        }
        await self.redis.set(check_key(enrollment_id, lesson_id), json.dumps(value), ex=TTL)
