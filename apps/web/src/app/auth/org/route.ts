import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

import { isAllowedOrigin } from "@/lib/auth/policy";
import { serverConfig } from "@/server/config";
import { csrfRejected, errorResponse } from "@/server/responses";
import { clearSession, readSession, writeOrganization, writeTokens } from "@/server/session";
import { accessTokenFor } from "@/server/tokens";

const bodySchema = z.object({ organizationId: z.uuid().nullable() });

/**
 * Switch the active organization. The choice is verified against the API (membership, or platform
 * admin) before it is stored in an httpOnly cookie; the /backend proxy then sends it as
 * X-Organization-Id on every API call, and the API re-checks it every time.
 */
export async function POST(request: NextRequest) {
  const cfg = serverConfig();
  if (!isAllowedOrigin("POST", request.headers, cfg.webOrigin)) return csrfRejected();
  const parsed = bodySchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return errorResponse(422, "validation_error", "Invalid organization id.");

  const session = await readSession(request.cookies);
  const access = await accessTokenFor(session);
  if (!access.accessToken) {
    const response = errorResponse(401, "unauthenticated", "Authentication is required.");
    if (access.expired) clearSession(response);
    return response;
  }

  const { organizationId } = parsed.data;
  if (organizationId) {
    const check = await fetch(`${cfg.apiInternalUrl}/api/v1/me`, {
      headers: {
        Authorization: `Bearer ${access.accessToken}`,
        "X-Organization-Id": organizationId,
      },
      cache: "no-store",
    });
    if (!check.ok) {
      return errorResponse(
        403,
        "organization_access_denied",
        "You are not a member of this organization.",
      );
    }
  }
  const response = new NextResponse(null, { status: 204 });
  writeOrganization(response, organizationId);
  if (access.refreshed) await writeTokens(response, access.refreshed);
  return response;
}
