/**
 * Same-origin proxy from the browser to the FastAPI backend: /backend/<path> -> API_INTERNAL_URL/<path>.
 *
 * This is the backend-for-frontend boundary:
 * - access tokens stay server-side (encrypted httpOnly cookies) and are attached here as
 *   `Authorization: Bearer`, refreshed shortly before they expire;
 * - the active organization (httpOnly cookie) is sent as `X-Organization-Id`;
 * - state-changing requests must come from our own origin (CSRF);
 * - only /api/v1/* and /health/* are reachable.
 * It runs at request time (unlike next.config rewrites), so one image works in every environment.
 */
import { NextResponse, type NextRequest } from "next/server";

import { isAllowedOrigin } from "@/lib/auth/policy";
import { serverConfig } from "@/server/config";
import { csrfRejected, errorResponse } from "@/server/responses";
import { clearSession, readSession, writeTokens } from "@/server/session";
import { accessTokenFor } from "@/server/tokens";

const ALLOWED_PREFIXES = ["api/v1/", "health/"];

// Hop-by-hop headers (RFC 9110 §7.6.1), browser credentials (the API authenticates via the bearer
// token we add, never via cookies), and headers only this proxy may set.
const STRIPPED_REQUEST_HEADERS = [
  "host",
  "connection",
  "keep-alive",
  "proxy-connection",
  "transfer-encoding",
  "upgrade",
  "te",
  "trailer",
  "content-length",
  "cookie",
  "authorization",
  "x-organization-id",
];
const STRIPPED_RESPONSE_HEADERS = [
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-encoding",
  "content-length",
  "set-cookie",
];

async function proxy(request: NextRequest, ctx: RouteContext<"/backend/[...path]">) {
  const cfg = serverConfig();
  const { path } = await ctx.params;
  const joined = path.map(encodeURIComponent).join("/");
  const hasDotSegment = path.some((segment) => segment === "." || segment === "..");
  if (hasDotSegment || !ALLOWED_PREFIXES.some((prefix) => `${joined}/`.startsWith(prefix))) {
    return errorResponse(404, "not_found", "Not Found");
  }
  if (!isAllowedOrigin(request.method, request.headers, cfg.webOrigin)) return csrfRejected();

  const session = await readSession(request.cookies);
  const access = await accessTokenFor(session);

  const headers = new Headers(request.headers);
  for (const name of STRIPPED_REQUEST_HEADERS) headers.delete(name);
  if (access.accessToken) headers.set("Authorization", `Bearer ${access.accessToken}`);
  if (session.organizationId) headers.set("X-Organization-Id", session.organizationId);

  const target = new URL(`${joined}${request.nextUrl.search}`, `${cfg.apiInternalUrl}/`);
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Required by Node's fetch when streaming a request body.
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
      signal: request.signal,
    } as RequestInit);
  } catch {
    return errorResponse(502, "upstream_unavailable", "The API is unreachable.");
  }

  const responseHeaders = new Headers(upstream.headers);
  for (const name of STRIPPED_RESPONSE_HEADERS) responseHeaders.delete(name);
  const response = new NextResponse(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
  if (access.refreshed) await writeTokens(response, access.refreshed);
  if (access.expired) clearSession(response);
  return response;
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
