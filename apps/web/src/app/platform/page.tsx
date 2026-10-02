import type { Metadata } from "next";

import { PlatformDashboard } from "@/features/platform/dashboard";

export const metadata: Metadata = { title: "Platform" };

export default function Page() {
  return <PlatformDashboard />;
}
