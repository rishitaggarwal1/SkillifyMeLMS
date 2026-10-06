import type { Metadata } from "next";
import { AdminOverview } from "@/features/admin/overview";
export const metadata: Metadata = { title: "College overview" };
export default function AdminIndex() {
  return <AdminOverview />;
}
