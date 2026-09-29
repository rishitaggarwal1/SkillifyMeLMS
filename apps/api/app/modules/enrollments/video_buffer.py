"""Atomic Redis watch buffer. Dirty entries never expire before Postgres acknowledges them.

The dirty set is sampled without removal: a worker crash cannot lose a claimed heartbeat.
Acknowledgement compares revisions, preserving writes arriving during a flush. Clean entries
expire after seven days; the next heartbeat seeds its bitmap from Postgres.
"""

import json
import time
from dataclasses import dataclass
from math import ceil
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from app.db.base import new_id

DIRTY = "progress:dirty"
CLEAN_TTL = 7 * 86400
SEGMENT_SECONDS = 5

_WRITE = """
local data = cjson.decode(ARGV[1])
if redis.call('EXISTS', KEYS[1]) == 0 then
  -- A new (or expired) entry must be seeded from Postgres: the caller retries with the baseline.
  if ARGV[2] == '?' then return 0 end
  local bytes = string.gsub(ARGV[2], '..', function(cc) return string.char(tonumber(cc,16)) end)
  redis.call('SET', KEYS[2], bytes)
  redis.call('DEL', KEYS[4])
end
redis.call('PERSIST', KEYS[1])
redis.call('PERSIST', KEYS[2])
redis.call('PERSIST', KEYS[4])
local position = data.position
local now = tonumber(ARGV[3])
-- Credit at most one heartbeat interval (x1.5 slack) per beat, and never more than the real time
-- since the previous beat allows, so rapid-fire heartbeats cannot inflate watch time.
local limit = tonumber(ARGV[4]) * data.rate
local previous = redis.call('GET', KEYS[1])
if previous then
  local previous_at = cjson.decode(previous).server_at
  if previous_at then limit = math.min(limit, math.max(0, now - previous_at) * 1.5 * data.rate) end
end
local played = math.min(data.played, limit, position)
data.server_at = now
-- Merge partial intervals within each segment, so pausing mid-segment loses no watch time.
-- Only fully traversed segments count. Seeking never fills a gap.
if played > 0 then
  local first = math.floor((position - played) / 5)
  local last = math.ceil(position / 5) - 1
  for segment = first, last do
    if redis.call('GETBIT', KEYS[2], segment) == 0 then
      local lower = segment * 5
      local upper = math.min(lower + 5, data.duration)
      local stored = redis.call('HGET', KEYS[4], tostring(segment))
      local ranges = stored and cjson.decode(stored) or {}
      table.insert(ranges, {math.max(lower, position - played), math.min(upper, position)})
      table.sort(ranges, function(a,b) return a[1] < b[1] end)
      local merged = {}
      for _, range in ipairs(ranges) do
        local prev = merged[#merged]
        if prev and range[1] <= prev[2] + 0.001 then
          prev[2] = math.max(prev[2], range[2])
        else table.insert(merged, range) end
      end
      if #merged == 1 and merged[1][1] <= lower + 0.001 and merged[1][2] >= upper - 0.001 then
        redis.call('SETBIT', KEYS[2], segment, 1)
        redis.call('HDEL', KEYS[4], tostring(segment))
      else
        -- Bound memory for highly fragmented/repeated seeks; dropping fragments is conservative.
        while #merged > 50 do table.remove(merged) end
        redis.call('HSET', KEYS[4], tostring(segment), cjson.encode(merged))
      end
    end
  end
end
redis.call('SET', KEYS[1], cjson.encode(data))
redis.call('SADD', KEYS[3], KEYS[1])
return 1
"""

_READ = """
local data = redis.call('GET', KEYS[1])
if not data then return nil end
local bytes = redis.call('GET', KEYS[2]) or ''
local hex = string.gsub(bytes, '.', function(c) return string.format('%02x', string.byte(c)) end)
return {data, hex}
"""

_ACK = """
local data = redis.call('GET', KEYS[1])
if data and cjson.decode(data).revision == ARGV[1] then
  redis.call('SREM', KEYS[3], KEYS[1])
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  redis.call('EXPIRE', KEYS[2], ARGV[2])
  redis.call('EXPIRE', KEYS[4], ARGV[2])
end
return 1
"""


def _now() -> float:
    """Wall-clock seconds, shared by all API replicas (NTP-synced). Tests replace this."""
    return time.time()


def buffer_key(enrollment_id: UUID, lesson_id: UUID, asset_id: UUID) -> str:
    return f"progress:video:{enrollment_id}:{lesson_id}:{asset_id}"


def watched_ratio(bitmap: bytes, duration: int) -> float:
    segments = ceil(duration / SEGMENT_SECONDS)
    whole, tail = divmod(segments, 8)
    count = sum(byte.bit_count() for byte in bitmap[:whole])
    if tail and whole < len(bitmap):
        count += (bitmap[whole] >> (8 - tail)).bit_count()
    watched = count * SEGMENT_SECONDS
    last = segments - 1
    if last >= 0 and last // 8 < len(bitmap) and bitmap[last // 8] & (1 << (7 - last % 8)):
        watched -= segments * SEGMENT_SECONDS - duration
    return watched / duration if duration else 0


@dataclass(frozen=True)
class BufferedVideo:
    key: str
    data: dict[str, Any]
    bitmap: bytes

    @property
    def ratio(self) -> float:
        return watched_ratio(self.bitmap, int(self.data["duration"]))


class VideoBuffer:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def write(
        self, key: str, data: dict[str, Any], baseline: bytes | None, *, interval_seconds: float
    ) -> bool:
        """Record one heartbeat. `interval_seconds` is the client's heartbeat interval; one beat
        is credited at most 1.5x that, and at most 1.5x the real time since the previous beat.

        `baseline` seeds a new entry's watched bitmap (from Postgres). With None, nothing is
        written when the entry doesn't exist yet and False is returned: load the baseline and call
        again. Checking and writing in one script means an entry expiring in between can never be
        re-created empty over the student's stored progress."""
        written = await self.redis.eval(
            _WRITE,
            4,
            key,
            f"{key}:bits",
            DIRTY,
            f"{key}:partials",
            json.dumps({**data, "revision": str(new_id())}),
            "?" if baseline is None else baseline.hex(),
            repr(_now()),
            repr(interval_seconds * 1.5),
        )
        return bool(written)

    async def read(self, key: str) -> BufferedVideo | None:
        row = await self.redis.eval(_READ, 2, key, f"{key}:bits")
        return BufferedVideo(key, json.loads(row[0]), bytes.fromhex(row[1])) if row else None

    async def pending(self, limit: int) -> list[BufferedVideo]:
        raw_keys = await self.redis.srandmember(DIRTY, limit)
        if not isinstance(raw_keys, list):
            return []
        keys = [k.decode() if isinstance(k, bytes) else str(k) for k in raw_keys]
        if not keys:
            return []
        pipe = self.redis.pipeline(transaction=False)
        for key in keys:
            pipe.eval(_READ, 2, key, f"{key}:bits")
        rows = await pipe.execute()
        return [
            BufferedVideo(key, json.loads(row[0]), bytes.fromhex(row[1]))
            for key, row in zip(keys, rows, strict=True)
            if row
        ]

    async def acknowledge(self, entries: list[BufferedVideo]) -> None:
        pipe = self.redis.pipeline(transaction=False)
        for entry in entries:
            pipe.eval(
                _ACK,
                4,
                entry.key,
                f"{entry.key}:bits",
                DIRTY,
                f"{entry.key}:partials",
                entry.data["revision"],
                CLEAN_TTL,
            )
        await pipe.execute()
