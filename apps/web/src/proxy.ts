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
  // Prefetches must not follow a redirect into /auth/login and overwrite an active PKCE flow.
  // Page shells expose no private data; the API still authorizes every prefetched request.
  matcher: [
    {
      source: "/admin/:path*",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
    {
      source: "/learn/:path*",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
    {
      source: "/platform/:path*",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
    {
      source: "/teach/:path*",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
