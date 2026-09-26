import type { Metadata } from "next";

import { MembersPage } from "@/features/admin/members";

export const metadata: Metadata = { title: "Members" };

export default function Page() {
  return <MembersPage />;
}
