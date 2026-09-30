import type { Metadata } from "next";

import { CoursePage } from "@/features/teach/course-page";

export const metadata: Metadata = { title: "Course" };

export default async function Page({ params }: PageProps<"/teach/courses/[courseId]">) {
  const { courseId } = await params;
  return <CoursePage courseId={courseId} />;
}
