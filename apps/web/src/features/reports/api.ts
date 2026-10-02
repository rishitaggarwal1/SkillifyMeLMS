import { infiniteQueryOptions } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import type { BatchCourseSummary, CourseProgressPage } from "@/lib/api/types";
import { unwrap } from "@/lib/api/unwrap";

type Page<T> = { items: T[]; next_cursor: string | null };

/** A batch's students in a course, a page at a time (columns come with every page). */
export const courseProgressQuery = (courseId: string, batchId: string) =>
  infiniteQueryOptions({
    queryKey: ["reports", "progress", courseId, batchId] as const,
    queryFn: ({ pageParam }): Promise<CourseProgressPage> =>
      unwrap(
        api.GET("/api/v1/courses/{course_id}/progress", {
          params: {
            path: { course_id: courseId },
            query: { batch_id: batchId, limit: 50, cursor: pageParam },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

/** Same-origin download through the /backend proxy (it adds the session). */
export function progressCsvUrl(courseId: string, batchId: string): string {
  return `/backend/api/v1/courses/${courseId}/progress.csv?batch_id=${encodeURIComponent(batchId)}`;
}

export const batchCoursesQuery = (batchId: string) =>
  infiniteQueryOptions({
    queryKey: ["reports", "batch-courses", batchId] as const,
    queryFn: ({ pageParam }): Promise<Page<BatchCourseSummary>> =>
      unwrap(
        api.GET("/api/v1/batches/{batch_id}/courses", {
          params: { path: { batch_id: batchId }, query: { limit: 50, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
