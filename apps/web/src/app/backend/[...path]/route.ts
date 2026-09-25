/**
 * Same-origin proxy from the browser to the FastAPI backend: /backend/<path> -> API_INTERNAL_URL/<path>.
 *
 * Runs at request time (unlike next.config rewrites, which are fixed at build time), so one Docker
 * image works in every environment. Later phases attach the user's session from an httpOnly cookie
 * here, so access tokens never reach browser JavaScript.
 */
import type { NextRequest } from "next/server";

const ALLOWED_PREFIXES = ["api/v1/", "health/"];

// Hop-by-hop headers must not be forwarded (RFC 9110 §7.6.1); host is set by fetch.
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
];
const STRIPPED_RESPONSE_HEADERS = [
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-encoding",
  "content-length",
];

function apiOrigin(): string {
  return process.env.API_INTERNAL_URL ?? "http://localhost:8000";
}

async function proxy(request: NextRequest, ctx: RouteContext<"/backend/[...path]">) {
  const { path } = await ctx.params;
  const joined = path.map(encodeURIComponent).join("/");
  const hasDotSegment = path.some((segment) => segment === "." || segment === "..");
  if (hasDotSegment || !ALLOWED_PREFIXES.some((prefix) => `${joined}/`.startsWith(prefix))) {
    return Response.json(
      { error: { code: "not_found", message: "Not Found", details: null } },
      { status: 404 },
    );
  }

  const target = new URL(`${joined}${request.nextUrl.search}`, `${apiOrigin()}/`);
  const headers = new Headers(request.headers);
  for (const name of STRIPPED_REQUEST_HEADERS) headers.delete(name);

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
    return Response.json(
      {
        error: {
          code: "upstream_unavailable",
          message: "The API is unreachable.",
          details: null,
        },
      },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers(upstream.headers);
  for (const name of STRIPPED_RESPONSE_HEADERS) responseHeaders.delete(name);
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
