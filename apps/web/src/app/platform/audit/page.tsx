import type { Metadata } from "next";

import { PlatformAuditPage } from "@/features/platform/audit";
import { uuidParam } from "@/lib/params";

export const metadata: Metadata = { title: "Audit log" };

export default async function Page({ searchParams }: PageProps<"/platform/audit">) {
  const { organization_id, actor_user_id } = await searchParams;
  return (
    <PlatformAuditPage
      organizationId={uuidParam(organization_id)}
      actorUserId={uuidParam(actor_user_id)}
    />
  );
}
