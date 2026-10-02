import type { Metadata } from "next";

import { OrganizationsPage } from "@/features/platform/organizations";

export const metadata: Metadata = { title: "Organizations" };

export default function Page() {
  return <OrganizationsPage />;
}
