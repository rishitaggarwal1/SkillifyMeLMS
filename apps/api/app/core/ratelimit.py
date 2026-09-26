"""Sliding-window rate limiting in Redis.

Each key is a sorted set of request timestamps (a sliding log). One Lua script prunes entries older
than the window, counts the rest and, if under the limit, records this request. Because it runs
atomically on the Redis server, concurrent API instances can't race past the limit. Unlike fixed
windows, there is no burst at window boundaries.
"""

import math
import time
from dataclasses import dataclass

from redis.asyncio import Redis
from uuid_utils.compat import uuid7

from app.core.errors import RateLimitedError

_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, member)
  redis.call('PEXPIRE', key, window)
  return {1, limit - count - 1, 0}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry_after = window - (now - tonumber(oldest[2]))
return {0, 0, retry_after}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimiter:
    def __init__(self, redis: "Redis", prefix: str = "ratelimit") -> None:
        self.redis = redis
        self.prefix = prefix
        self._script = redis.register_script(_SLIDING_WINDOW)

    async def hit(
        self, bucket: str, key: str, *, limit: int, window_seconds: int
    ) -> RateLimitResult:
        """Record one attempt; returns whether it was allowed."""
        now_ms = int(time.time() * 1000)
        allowed, remaining, retry_ms = await self._script(
            keys=[f"{self.prefix}:{bucket}:{key}"],
            args=[now_ms, window_seconds * 1000, limit, f"{now_ms}-{uuid7().hex}"],
        )
        return RateLimitResult(
            allowed=bool(allowed),
            remaining=int(remaining),
            retry_after_seconds=max(1, math.ceil(int(retry_ms) / 1000)) if not allowed else 0,
        )

    async def check(self, bucket: str, key: str, *, limit: int, window_seconds: int) -> None:
        """Record one attempt, raising 429 (with Retry-After) when over the limit."""
        result = await self.hit(bucket, key, limit=limit, window_seconds=window_seconds)
        if not result.allowed:
            raise RateLimitedError(
                details={"retry_after_seconds": result.retry_after_seconds},
                headers={"Retry-After": str(result.retry_after_seconds)},
            )

    async def is_blocked(self, bucket: str, key: str, *, limit: int, window_seconds: int) -> int:
        """Seconds until `key` may try again (0 if not blocked), without recording an attempt."""
        full_key = f"{self.prefix}:{bucket}:{key}"
        now_ms = int(time.time() * 1000)
        await self.redis.zremrangebyscore(full_key, "-inf", now_ms - window_seconds * 1000)
        if await self.redis.zcard(full_key) < limit:
            return 0
        oldest = await self.redis.zrange(full_key, 0, 0, withscores=True)
        if not oldest:
            return 0
        retry_ms = window_seconds * 1000 - (now_ms - int(oldest[0][1]))
        return max(1, math.ceil(retry_ms / 1000))
