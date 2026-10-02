import type { NextRequest } from "next/server";

import { resolveCourseLink } from "@/server/course-links";

/** Shareable lesson link: redirects into the student's enrollment (404 if not enrolled). */
export async function GET(
  request: NextRequest,
  ctx: RouteContext<"/learn/courses/[courseId]/lessons/[lessonId]">,
) {
  const { courseId, lessonId } = await ctx.params;
  return resolveCourseLink(request, courseId, lessonId);
}
