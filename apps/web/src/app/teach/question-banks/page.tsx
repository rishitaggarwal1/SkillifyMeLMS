import type { Metadata } from "next";
import { EmptyState, PageTitle } from "@/features/admin/ui";
export const metadata: Metadata = { title: "Question banks" };
export default function QuestionBanksPage() {
  return (
    <div className="flex flex-col gap-4">
      <PageTitle title="Question banks" />
      <EmptyState href="/teach/courses" actionLabel="View courses">
        Question bank editing is coming in the next instructor UI step.
      </EmptyState>
    </div>
  );
}
