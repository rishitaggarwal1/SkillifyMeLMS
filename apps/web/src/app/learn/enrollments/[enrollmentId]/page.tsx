import type { Metadata } from "next";

import { ResumeCourse } from "@/features/learn/course-player";

export const metadata: Metadata = { title: "Course" };

export default async function Page({ params }: PageProps<"/learn/enrollments/[enrollmentId]">) {
  const { enrollmentId } = await params;
  return <ResumeCourse enrollmentId={enrollmentId} />;
}
