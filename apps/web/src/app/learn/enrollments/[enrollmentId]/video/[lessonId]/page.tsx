import { redirect } from "next/navigation";

/** The step 3 video page, kept as a redirect: video lessons now play inside the course player. */
export default async function VideoPage({
  params,
}: PageProps<"/learn/enrollments/[enrollmentId]/video/[lessonId]">) {
  const { enrollmentId, lessonId } = await params;
  redirect(`/learn/enrollments/${enrollmentId}/lessons/${lessonId}`);
}
