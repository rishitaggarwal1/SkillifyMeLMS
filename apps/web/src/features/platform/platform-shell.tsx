"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/features/auth/queries";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/platform", label: "Dashboard", exact: true },
  { href: "/platform/organizations", label: "Organizations" },
  { href: "/platform/users", label: "Users" },
  { href: "/platform/courses", label: "Courses" },
  { href: "/platform/audit", label: "Audit log" },
];

/** Platform-admin area (SkillifyMe operators). The API checks the role on every call; this only
 * keeps everyone else from seeing a broken page. */
export function PlatformShell({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();
  const pathname = usePathname();

  if (isPending) return <Skeleton className="h-40 w-full" />;
  if (!me) return null; // proxy.ts already sent signed-out visitors to the login page
  if (!me.is_platform_admin) {
    return (
      <Alert variant="destructive">
        <AlertTitle>No access</AlertTitle>
        <AlertDescription>This area is for platform admins.</AlertDescription>
      </Alert>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <nav aria-label="Platform" className="-mx-1 flex gap-1 overflow-x-auto">
        {NAV.map((item) => {
          const current = item.exact ? pathname === item.href : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={current ? "page" : undefined}
              className={cn(
                "shrink-0 rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground",
                "hover:bg-muted hover:text-foreground aria-[current=page]:bg-muted aria-[current=page]:text-foreground",
              )}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      {children}
    </div>
  );
}
