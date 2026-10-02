import { UserDetailPage } from "@/features/platform/users";

export default async function Page({ params }: PageProps<"/platform/users/[userId]">) {
  const { userId } = await params;
  return <UserDetailPage userId={userId} />;
}
