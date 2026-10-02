import { OrganizationDetailPage } from "@/features/platform/organization-detail";

export default async function Page({
  params,
}: PageProps<"/platform/organizations/[organizationId]">) {
  const { organizationId } = await params;
  return <OrganizationDetailPage organizationId={organizationId} />;
}
