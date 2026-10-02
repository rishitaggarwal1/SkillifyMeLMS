import "server-only";

import { NextResponse, type NextRequest } from "next/server";

import { serverConfig } from "./config";
import { clearSession, readSession, writeTokens } from "./session";
import { accessTokenFor } from "./tokens";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function notFound(): NextResponse {
  return new NextResponse("Course not found (or you aren't enrolled in it).", {
    status: 404,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}

/**
 * Stable, shareable course links: /learn/courses/{courseId}[/lessons/{lessonId}] find the signed-in
 * student's active enrollment in that course (through the API, as the student) and redirect to the
 * canonical /learn/enrollments/{id} URL. 404 when there is none. Redirect targets are built only
 * from validated UUIDs, so these routes can't be used as open redirects.
 */
export async function resolveCourseLink(
  request: NextRequest,
  courseId: string,
  lessonId?: string,
): Promise<NextResponse> {
  if (!UUID.test(courseId) || (lessonId !== undefined && !UUID.test(lessonId))) return notFound();
  const cfg = serverConfig();
  const session = await readSession(request.cookies);
  const access = await accessTokenFor(session);
  if (!access.accessToken) {
    // proxy.ts sends visitors without a session to sign in; this is an expired one.
    const login = new URL("/auth/login", cfg.webOrigin);
    login.searchParams.set("returnTo", request.nextUrl.pathname);
    const response = NextResponse.redirect(login);
    if (access.expired) clearSession(response);
    return response;
  }
  const headers = new Headers({ Authorization: `Bearer ${access.accessToken}` });
  if (session.organizationId) headers.set("X-Organization-Id", session.organizationId);
  const url = new URL(`${cfg.apiInternalUrl}/api/v1/enrollments`);
  url.searchParams.set("course_id", courseId);
  url.searchParams.set("limit", "1");
  let enrollmentId: string | undefined;
  try {
    const upstream = await fetch(url, { headers, cache: "no-store" });
    if (upstream.ok) {
      const page = (await upstream.json()) as { items?: { id?: unknown }[] };
      const id = page.items?.[0]?.id;
      if (typeof id === "string" && UUID.test(id)) enrollmentId = id;
    }
  } catch {
    return NextResponse.json(
      {
        error: { code: "upstream_unavailable", message: "The API is unreachable.", details: null },
      },
      { status: 502 },
    );
  }
  if (!enrollmentId) return notFound();
  const target = lessonId
    ? `/learn/enrollments/${enrollmentId}/lessons/${lessonId}`
    : `/learn/enrollments/${enrollmentId}`;
  // The public origin, not request.url: behind the standalone server request.url carries the
  // bind address (e.g. 0.0.0.0:3000), which would send the browser somewhere else.
  const response = NextResponse.redirect(new URL(target, cfg.webOrigin), 307);
  if (access.refreshed) await writeTokens(response, access.refreshed);
  return response;
}
