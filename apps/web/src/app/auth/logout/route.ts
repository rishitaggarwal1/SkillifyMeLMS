import { NextResponse, type NextRequest } from "next/server";

import { isAllowedOrigin } from "@/lib/auth/policy";
import { serverConfig } from "@/server/config";
import { logoutUrl } from "@/server/oidc";
import { csrfRejected } from "@/server/responses";
import { clearSession, readSession, writeOrganization } from "@/server/session";

/** POST only (a form in the user menu), so a cross-site link can't log people out. */
export async function POST(request: NextRequest) {
  if (!isAllowedOrigin("POST", request.headers, serverConfig().webOrigin)) return csrfRejected();
  const session = await readSession(request.cookies);
  // End the Keycloak SSO session too; Keycloak then redirects back to the home page.
  const response = NextResponse.redirect(logoutUrl(session.idToken), 303);
  clearSession(response);
  writeOrganization(response, null);
  return response;
}
