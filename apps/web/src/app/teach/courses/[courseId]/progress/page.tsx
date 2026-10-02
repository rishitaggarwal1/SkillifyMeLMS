import type { Metadata } from "next";

import { uuidParam } from "@/lib/params";
import { CourseProgressPage } from "@/features/reports/course-progress-page";

export const metadata: Metadata = { title: "Progress" };

export default async function Page({
  params,
  searchParams,
}: PageProps<"/teach/courses/[courseId]/progress">) {
  const { courseId } = await params;
  const { batch } = await searchParams;
  return <CourseProgressPage courseId={courseId} batchId={uuidParam(batch)} />;
}
