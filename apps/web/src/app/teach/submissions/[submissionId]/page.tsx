import type { Metadata } from "next";

import { SubmissionReviewPage } from "@/features/teach/grading";

export const metadata: Metadata = { title: "Grade submission" };

export default async function Page({
  params,
  searchParams,
}: PageProps<"/teach/submissions/[submissionId]">) {
  const { submissionId } = await params;
  const { from } = await searchParams;
  return <SubmissionReviewPage submissionId={submissionId} fromGrading={from === "grading"} />;
}
