import type { Metadata } from "next";

import { Dashboard } from "@/features/learn/dashboard";

export const metadata: Metadata = { title: "My learning" };

export default function LearnPage() {
  return <Dashboard />;
}
