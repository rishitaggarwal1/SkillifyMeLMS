import type { Metadata } from "next";

import { PageTitle } from "@/features/admin/ui";
import { VideoUpload } from "@/features/video/video-upload";

export const metadata: Metadata = { title: "Videos" };

export default function VideosPage() {
  return (
    <div className="flex max-w-2xl flex-col gap-4">
      <PageTitle title="Upload a lesson video" />
      <VideoUpload />
    </div>
  );
}
