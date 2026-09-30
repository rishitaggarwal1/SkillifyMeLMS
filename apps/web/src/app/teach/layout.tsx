import { TeachShell } from "@/features/teach/teach-shell";

export default function TeachLayout({ children }: LayoutProps<"/teach">) {
  return <TeachShell>{children}</TeachShell>;
}
