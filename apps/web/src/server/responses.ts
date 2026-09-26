import "server-only";

import { NextResponse } from "next/server";

/** The API's error envelope, for errors raised by the BFF itself. */
export function errorResponse(
  status: number,
  code: string,
  message: string,
  headers?: Record<string, string>,
): NextResponse {
  return NextResponse.json({ error: { code, message, details: null } }, { status, headers });
}

export function rateLimited(retryAfterSeconds: number): NextResponse {
  return errorResponse(429, "rate_limited", "Too many requests. Try again later.", {
    "Retry-After": String(retryAfterSeconds),
  });
}

export function csrfRejected(): NextResponse {
  return errorResponse(403, "csrf_rejected", "Cross-site request rejected.");
}
