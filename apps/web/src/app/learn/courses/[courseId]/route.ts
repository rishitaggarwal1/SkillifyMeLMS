import type { NextRequest } from "next/server";

import { resolveCourseLink } from "@/server/course-links";

/** Shareable course link: redirects to the student's enrollment (404 if not enrolled). */
export async function GET(request: NextRequest, ctx: RouteContext<"/learn/courses/[courseId]">) {
  const { courseId } = await ctx.params;
  return resolveCourseLink(request, courseId);
}
