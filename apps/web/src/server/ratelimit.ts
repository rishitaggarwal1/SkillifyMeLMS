import "server-only";

import Redis from "ioredis";

import { serverConfig } from "./config";

/**
 * Sliding-window limiter for the BFF's auth routes (same algorithm as the API's
 * app/core/ratelimit.py: a sorted-set log pruned and counted atomically in one Lua script).
 * Fails open if Redis is unavailable: login availability matters more than throttling, and
 * Keycloak has its own brute-force protection for password attempts.
 */
const SLIDING_WINDOW = `
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, ARGV[4])
  redis.call('PEXPIRE', key, window)
  return {1, 0}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
return {0, window - (now - tonumber(oldest[2]))}
`;

let redis: Redis | null | undefined;

function connection(): Redis | null {
  if (redis !== undefined) return redis;
  const url = serverConfig().redisUrl;
  redis = url ? new Redis(url, { maxRetriesPerRequest: 1, lazyConnect: false }) : null;
  redis?.on("error", () => undefined); // surfaced as a failed eval below
  return redis;
}

export type LimitResult = { allowed: boolean; retryAfterSeconds: number };

export async function hitAuthLimit(bucket: string, clientKey: string): Promise<LimitResult> {
  const conn = connection();
  if (!conn) return { allowed: true, retryAfterSeconds: 0 };
  const now = Date.now();
  try {
    const [allowed, retryMs] = (await conn.eval(
      SLIDING_WINDOW,
      1,
      `bff-ratelimit:${bucket}:${clientKey}`,
      now,
      60_000,
      serverConfig().authRateLimitPerMinute,
      `${now}-${crypto.randomUUID()}`,
    )) as [number, number];
    return { allowed: allowed === 1, retryAfterSeconds: Math.max(1, Math.ceil(retryMs / 1000)) };
  } catch {
    return { allowed: true, retryAfterSeconds: 0 };
  }
}

/** Best-effort client identifier: the first X-Forwarded-For hop (set by the edge), else unknown. */
export function clientKey(headers: Headers): string {
  return (
    headers.get("x-forwarded-for")?.split(",")[0]?.trim() || headers.get("x-real-ip") || "unknown"
  );
}
