import type { Metadata } from "next";

import { ImportsPage } from "@/features/admin/imports";

export const metadata: Metadata = { title: "Import students" };

export default async function Page({ searchParams }: PageProps<"/admin/imports">) {
  const { batch } = await searchParams;
  return <ImportsPage initialBatchId={typeof batch === "string" ? batch : undefined} />;
}
