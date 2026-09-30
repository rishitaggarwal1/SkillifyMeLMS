import type { Metadata } from "next";

import { CourseList } from "@/features/teach/course-list";

export const metadata: Metadata = { title: "Courses" };

export default function CoursesPage() {
  return <CourseList />;
}
