import { VideoUpload } from "@/features/video/video-upload";

export default function VideosPage() {
  return (
    <main className="mx-auto max-w-2xl space-y-4 p-4">
      <h1 className="text-xl font-semibold">Upload a lesson video</h1>
      <VideoUpload />
    </main>
  );
}
