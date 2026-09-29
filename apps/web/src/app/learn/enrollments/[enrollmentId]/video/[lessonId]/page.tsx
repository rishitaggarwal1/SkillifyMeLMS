import { EnrollmentVideo } from "@/features/video/enrollment-video";

export default async function VideoPage({
  params,
}: {
  params: Promise<{ enrollmentId: string; lessonId: string }>;
}) {
  const { enrollmentId, lessonId } = await params;
  return (
    <main className="mx-auto max-w-4xl space-y-4 p-4">
      <h1 className="text-xl font-semibold">Lesson video</h1>
      <EnrollmentVideo enrollmentId={enrollmentId} lessonId={lessonId} />
    </main>
  );
}
