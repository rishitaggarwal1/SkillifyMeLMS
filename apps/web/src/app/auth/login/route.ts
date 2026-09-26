import { NextResponse, type NextRequest } from "next/server";

import { safeReturnTo } from "@/lib/auth/policy";
import { startLogin } from "@/server/oidc";
import { clientKey, hitAuthLimit } from "@/server/ratelimit";
import { rateLimited } from "@/server/responses";
import { writeLoginState } from "@/server/session";

/** Starts the Keycloak login (authorization code + PKCE). `?returnTo=/path` is honoured if local. */
export async function GET(request: NextRequest) {
  const limit = await hitAuthLimit("login", clientKey(request.headers));
  if (!limit.allowed) return rateLimited(limit.retryAfterSeconds);

  const { url, verifier, state, nonce } = await startLogin();
  const response = NextResponse.redirect(url, 303);
  await writeLoginState(response, {
    verifier,
    state,
    nonce,
    returnTo: safeReturnTo(request.nextUrl.searchParams.get("returnTo")),
  });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
