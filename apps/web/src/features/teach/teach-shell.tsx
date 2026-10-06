"use client";

import { RoleFrame } from "@/components/patterns/role-frame";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PageSkeleton } from "@/components/patterns/states";
import { hasPermission, useMe } from "@/features/auth/queries";

/** Instructor area: an active organization and course access (authors, and org admins who
 * distribute courses assigned to their org). The API authorizes every request regardless. */
export function TeachShell({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();

  if (isPending) return <PageSkeleton />;
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
  return <RoleFrame area="teach">{children}</RoleFrame>;
}

/** Whether the active organization is a content publisher (can grant courses to other orgs). */
export function useIsPublisher(): boolean {
  const { data: me } = useMe();
  const active = me?.memberships.find((m) => m.organization.id === me.active_organization_id);
  return !!active?.organization.is_content_publisher;
}
