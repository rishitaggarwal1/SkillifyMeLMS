import type { Metadata } from "next";

import { UsersPage } from "@/features/platform/users";
import { uuidParam } from "@/lib/params";

export const metadata: Metadata = { title: "Users" };

export default async function Page({ searchParams }: PageProps<"/platform/users">) {
  const { organization_id } = await searchParams;
  return <UsersPage organizationId={uuidParam(organization_id)} />;
}
