import type { Metadata } from "next";
import { QuestionBanksPage } from "@/features/teach/question-banks";
export const metadata: Metadata = { title: "Question banks" };
export default function Page() {
  return <QuestionBanksPage />;
}
