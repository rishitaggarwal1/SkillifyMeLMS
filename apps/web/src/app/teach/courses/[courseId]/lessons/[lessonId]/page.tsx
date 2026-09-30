import type { Metadata } from "next";

import { LessonPage } from "@/features/teach/lesson-page";

export const metadata: Metadata = { title: "Lesson" };

export default async function Page({
  params,
}: PageProps<"/teach/courses/[courseId]/lessons/[lessonId]">) {
  const { courseId, lessonId } = await params;
  return <LessonPage courseId={courseId} lessonId={lessonId} />;
}
