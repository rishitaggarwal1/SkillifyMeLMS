import type { Metadata } from "next";

import { SubmissionReviewPage } from "@/features/teach/grading";

export const metadata: Metadata = { title: "Grade submission" };

export default async function Page({ params }: PageProps<"/teach/submissions/[submissionId]">) {
  const { submissionId } = await params;
  return <SubmissionReviewPage submissionId={submissionId} />;
}
