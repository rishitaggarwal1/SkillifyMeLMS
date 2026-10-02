import { NextResponse, type NextRequest } from "next/server";

/**
 * Route protection: signed-out visitors to app areas are sent to the Keycloak login first (and
 * come back to the page they asked for). This only checks that a session cookie exists; the API
 * authorizes every request, so a stale or forged cookie gets nothing.
 */
const SESSION_COOKIES = ["__Host-sm_at", "__Host-sm_rt"];

export function proxy(request: NextRequest) {
  if (SESSION_COOKIES.some((name) => request.cookies.has(name))) return NextResponse.next();
  const login = new URL("/auth/login", request.url);
  login.searchParams.set("returnTo", `${request.nextUrl.pathname}${request.nextUrl.search}`);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/admin/:path*", "/learn/:path*", "/platform/:path*", "/teach/:path*"],
};
