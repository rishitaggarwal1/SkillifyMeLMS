import type { Metadata } from "next";

import { AdminCoursesPage } from "@/features/admin/courses";

export const metadata: Metadata = { title: "Courses" };

export default function Page() {
  return <AdminCoursesPage />;
}
