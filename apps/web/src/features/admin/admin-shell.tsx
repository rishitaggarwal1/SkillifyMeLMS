"use client";

import { RoleFrame } from "@/components/patterns/role-frame";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PageSkeleton } from "@/components/patterns/states";
import { hasPermission, useMe } from "@/features/auth/queries";

/** Org-admin area: needs an active organization and the batch.manage permission. */
export function AdminShell({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();

  if (isPending) return <PageSkeleton />;
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
  return <RoleFrame area="admin">{children}</RoleFrame>;
}
