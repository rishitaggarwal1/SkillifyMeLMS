"use client";

import { RoleFrame } from "@/components/patterns/role-frame";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PageSkeleton } from "@/components/patterns/states";
import { useMe } from "@/features/auth/queries";

/** Student area: needs an active organization (enrollments belong to the student's org). */
export function LearnShell({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();
  if (isPending) return <PageSkeleton />;
  if (!me) return null; // proxy.ts already sent signed-out visitors to the login page
  if (!me.active_organization_id) {
    return (
      <Alert>
        <AlertTitle>Choose an organization</AlertTitle>
        <AlertDescription>
          Use the organization menu at the top to pick your college.
        </AlertDescription>
      </Alert>
    );
  }
  return <RoleFrame area="learn">{children}</RoleFrame>;
}
