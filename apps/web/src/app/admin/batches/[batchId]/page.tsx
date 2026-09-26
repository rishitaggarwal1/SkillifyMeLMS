import { BatchDetailPage } from "@/features/admin/batch-detail";

export default async function Page({ params }: PageProps<"/admin/batches/[batchId]">) {
  const { batchId } = await params;
  return <BatchDetailPage batchId={batchId} />;
}
