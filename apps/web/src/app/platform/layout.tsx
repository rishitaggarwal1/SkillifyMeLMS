import { PlatformShell } from "@/features/platform/platform-shell";

export default function PlatformLayout({ children }: LayoutProps<"/platform">) {
  return <PlatformShell>{children}</PlatformShell>;
}
