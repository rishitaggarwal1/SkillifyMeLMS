"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { hasPermission, useMe } from "@/features/auth/queries";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/admin/courses", label: "Courses" },
  { href: "/admin/batches", label: "Batches" },
  { href: "/admin/members", label: "Members" },
  { href: "/admin/imports", label: "Import" },
];

/** Org-admin area: needs an active organization and the batch.manage permission. */
export function AdminShell({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();
  const pathname = usePathname();

  if (isPending) return <Skeleton className="h-40 w-full" />;
  if (!me) return null; // proxy.ts already sent signed-out visitors to the login page
  if (!me.active_organization_id) {
    return (
      <Alert>
        <AlertTitle>Choose an organization</AlertTitle>
        <AlertDescription>
          Use the organization menu at the top to pick where you work.
        </AlertDescription>
      </Alert>
    );
  }
  if (!hasPermission(me, "batch.manage")) {
    return (
      <Alert variant="destructive">
        <AlertTitle>No access</AlertTitle>
        <AlertDescription>This area is for organization admins.</AlertDescription>
      </Alert>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <nav aria-label="Admin" className="-mx-1 flex gap-1 overflow-x-auto">
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            aria-current={pathname.startsWith(item.href) ? "page" : undefined}
            className={cn(
              "shrink-0 rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground",
              "hover:bg-muted hover:text-foreground aria-[current=page]:bg-muted aria-[current=page]:text-foreground",
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>
      {children}
    </div>
  );
}
