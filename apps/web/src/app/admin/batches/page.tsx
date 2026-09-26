import type { Metadata } from "next";

import { BatchesPage } from "@/features/admin/batches";

export const metadata: Metadata = { title: "Batches" };

export default function Page() {
  return <BatchesPage />;
}
