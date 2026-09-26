import { NextResponse, type NextRequest } from "next/server";

import { serverConfig } from "@/server/config";
import { completeLogin } from "@/server/oidc";
import { clientKey, hitAuthLimit } from "@/server/ratelimit";
import { rateLimited } from "@/server/responses";
import { COOKIES, readLoginState, writeTokens } from "@/server/session";

function redirectHome(error: string): NextResponse {
  const url = new URL("/", serverConfig().webOrigin);
  url.searchParams.set("auth_error", error);
  const response = NextResponse.redirect(url, 303);
  response.cookies.set(COOKIES.login, "", { path: "/", maxAge: 0, secure: true, httpOnly: true });
  return response;
}

/** Keycloak redirects here with ?code&state. Exchanges the code and stores the tokens in cookies. */
export async function GET(request: NextRequest) {
  const limit = await hitAuthLimit("callback", clientKey(request.headers));
  if (!limit.allowed) return rateLimited(limit.retryAfterSeconds);

  const params = request.nextUrl.searchParams;
  if (params.has("error")) return redirectHome(params.get("error") ?? "login_failed");
  const login = await readLoginState(request.cookies);
  if (!login) return redirectHome("login_expired");

  try {
    const tokens = await completeLogin(params, login);
    const response = NextResponse.redirect(new URL(login.returnTo, serverConfig().webOrigin), 303);
    await writeTokens(response, tokens);
    response.cookies.set(COOKIES.login, "", { path: "/", maxAge: 0, secure: true, httpOnly: true });
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch {
    // State/nonce/PKCE mismatch, replayed or expired code, or Keycloak unreachable.
    return redirectHome("login_failed");
  }
}
