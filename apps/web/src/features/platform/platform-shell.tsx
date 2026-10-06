"use client";

import { RoleFrame } from "@/components/patterns/role-frame";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { PageSkeleton } from "@/components/patterns/states";
import { useMe } from "@/features/auth/queries";

/** Platform-admin area (SkillifyMe operators). The API checks the role on every call; this only
 * keeps everyone else from seeing a broken page. */
export function PlatformShell({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();

  if (isPending) return <PageSkeleton />;
  if (!me) return null; // proxy.ts already sent signed-out visitors to the login page
  if (!me.is_platform_admin) {
    return (
      <Alert variant="destructive">
        <AlertTitle>No access</AlertTitle>
        <AlertDescription>This area is for platform admins.</AlertDescription>
      </Alert>
    );
  }
  return <RoleFrame area="platform">{children}</RoleFrame>;
}
