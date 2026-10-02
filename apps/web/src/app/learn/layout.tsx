import { LearnShell } from "@/features/learn/learn-shell";

export default function LearnLayout({ children }: LayoutProps<"/learn">) {
  return <LearnShell>{children}</LearnShell>;
}
