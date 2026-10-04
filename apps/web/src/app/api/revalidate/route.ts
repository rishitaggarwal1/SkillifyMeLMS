/**
 * On-demand revalidation of statically generated pages, called by the API's Celery task after a
 * publish or archive commits. Protected by REVALIDATE_SECRET (header `x-revalidate-secret`), and
 * limited to an allow-list of tags.
 */
import { revalidatePath, revalidateTag } from "next/cache";
import type { NextRequest } from "next/server";

import { decideRevalidation } from "@/features/catalog/revalidate";

export async function POST(request: NextRequest) {
  let body: unknown = null;
  try {
    body = await request.json();
  } catch {
    // Invalid JSON is rejected below as invalid tags (after the secret check).
  }
  const decision = decideRevalidation(
    process.env.REVALIDATE_SECRET,
    request.headers.get("x-revalidate-secret"),
    body,
  );
  if (!decision.ok) {
    return Response.json({ error: decision.error }, { status: decision.status });
  }
  // Called from another service (not a Server Action): expire now, so the next visitor gets the
  // new catalog instead of a stale page (docs: revalidateTag, `{ expire: 0 }`).
  for (const tag of decision.tags) revalidateTag(tag, { expire: 0 });
  // A build without API settings prerenders an empty catalog before making a tagged fetch.
  // Invalidate its route too: that fallback has no data-tag dependency to expire.
  revalidatePath("/catalog");
  revalidatePath("/catalog/[slug]", "page");
  return Response.json({ revalidated: decision.tags });
}
