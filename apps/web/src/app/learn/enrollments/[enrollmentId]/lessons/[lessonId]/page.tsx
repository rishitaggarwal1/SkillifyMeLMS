import type { Metadata } from "next";

import { CoursePlayer } from "@/features/learn/course-player";

export const metadata: Metadata = { title: "Lesson" };

export default async function Page({
  params,
}: PageProps<"/learn/enrollments/[enrollmentId]/lessons/[lessonId]">) {
  const { enrollmentId, lessonId } = await params;
  return <CoursePlayer enrollmentId={enrollmentId} lessonId={lessonId} />;
}
