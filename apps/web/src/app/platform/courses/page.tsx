import type { Metadata } from "next";

import { PlatformCoursesPage } from "@/features/platform/courses";
import { uuidParam } from "@/lib/params";

export const metadata: Metadata = { title: "Courses" };

export default async function Page({ searchParams }: PageProps<"/platform/courses">) {
  const { organization_id } = await searchParams;
  return <PlatformCoursesPage organizationId={uuidParam(organization_id)} />;
}
