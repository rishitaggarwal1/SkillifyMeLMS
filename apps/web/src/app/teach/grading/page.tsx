import type { Metadata } from "next";
import { CrossCourseGrading } from "@/features/teach/overview";
export const metadata: Metadata = { title: "Grading" };
export default function GradingPage() {
  return <CrossCourseGrading />;
}
