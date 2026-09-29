import { createHash, timingSafeEqual } from "node:crypto";

import { z } from "zod";

import { CATALOG_TAG } from "./tags";

/** Tags the API may revalidate. Anything else is rejected, so the endpoint can't flush arbitrary
 * caches even with the secret. */
export const REVALIDATABLE_TAGS = [CATALOG_TAG] as const;

const bodySchema = z.object({
  tags: z.array(z.enum(REVALIDATABLE_TAGS)).min(1).max(10),
});

export type RevalidationDecision =
  { ok: true; tags: string[] } | { ok: false; status: 400 | 401 | 503; error: string };

function digest(value: string): Buffer {
  return createHash("sha256").update(value).digest();
}

/** Constant-time comparison (hashing first makes the lengths equal). */
export function secretMatches(provided: string | null, expected: string): boolean {
  if (provided === null) return false;
  return timingSafeEqual(digest(provided), digest(expected));
}

export function decideRevalidation(
  configuredSecret: string | undefined,
  providedSecret: string | null,
  body: unknown,
): RevalidationDecision {
  if (!configuredSecret) return { ok: false, status: 503, error: "revalidation_disabled" };
  if (!secretMatches(providedSecret, configuredSecret)) {
    return { ok: false, status: 401, error: "unauthenticated" };
  }
  const parsed = bodySchema.safeParse(body);
  if (!parsed.success) return { ok: false, status: 400, error: "invalid_tags" };
  return { ok: true, tags: [...new Set(parsed.data.tags)] };
}
