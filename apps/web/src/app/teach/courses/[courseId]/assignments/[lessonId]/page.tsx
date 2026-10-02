import type { Metadata } from "next";

import { SubmissionsPage } from "@/features/teach/grading";

export const metadata: Metadata = { title: "Submissions" };

export default async function Page({
  params,
}: PageProps<"/teach/courses/[courseId]/assignments/[lessonId]">) {
  const { courseId, lessonId } = await params;
  return <SubmissionsPage courseId={courseId} lessonId={lessonId} />;
}
