"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { hasPermission, useMe } from "@/features/auth/queries";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/teach/courses", label: "Courses" },
  { href: "/teach/videos", label: "Videos" },
];

/** Instructor area: an active organization and course access (authors, and org admins who
 * distribute courses assigned to their org). The API authorizes every request regardless. */
export function TeachShell({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();
  const pathname = usePathname();

  if (isPending) return <Skeleton className="h-40 w-full" />;
  if (!me) return null; // proxy.ts sends signed-out visitors to the login page
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
  if (!hasPermission(me, "course.read")) {
    return (
      <Alert variant="destructive">
        <AlertTitle>No access</AlertTitle>
        <AlertDescription>This area is for instructors and organization admins.</AlertDescription>
      </Alert>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      <nav aria-label="Teaching" className="-mx-1 flex gap-1 overflow-x-auto">
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

/** Whether the active organization is a content publisher (can grant courses to other orgs). */
export function useIsPublisher(): boolean {
  const { data: me } = useMe();
  const active = me?.memberships.find((m) => m.organization.id === me.active_organization_id);
  return !!active?.organization.is_content_publisher;
}
