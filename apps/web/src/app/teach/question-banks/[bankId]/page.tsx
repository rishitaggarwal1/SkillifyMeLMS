import type { Metadata } from "next";

import { QuestionBankPage } from "@/features/teach/question-banks";

export const metadata: Metadata = { title: "Question bank" };
export default async function Page({ params }: PageProps<"/teach/question-banks/[bankId]">) {
  const { bankId } = await params;
  return <QuestionBankPage bankId={bankId} />;
}
