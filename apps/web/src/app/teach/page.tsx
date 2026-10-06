import type { Metadata } from "next";
import { CourseList } from "@/features/teach/course-list";
export const metadata: Metadata = { title: "Teaching" };
export default function TeachIndex() {
  return <CourseList landing />;
}
