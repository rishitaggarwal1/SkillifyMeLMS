import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/unwrap";

import type { Enrollment, EnrollmentDetail } from "./outline";

const lessonPath = (enrollmentId: string, lessonId: string) => ({
  enrollment_id: enrollmentId,
  lesson_id: lessonId,
});

export const learnKeys = {
  enrollments: ["learn", "enrollments"] as const,
  enrollment: (id: string) => ["learn", "enrollments", id] as const,
};

/** The student's enrollments in the active organization (all pages; a student has few). */
export const myEnrollmentsQuery = () =>
  queryOptions({
    queryKey: learnKeys.enrollments,
    queryFn: async (): Promise<Enrollment[]> => {
      const all: Enrollment[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < 10; page++) {
        const result = await unwrap(
          api.GET("/api/v1/enrollments", { params: { query: { limit: 100, cursor } } }),
        );
        all.push(...result.items);
        if (!result.next_cursor) break;
        cursor = result.next_cursor;
      }
      return all;
    },
  });

/** The pinned version's outline and the student's progress (one request). */
export const enrollmentQuery = (enrollmentId: string) =>
  queryOptions({
    queryKey: learnKeys.enrollment(enrollmentId),
    queryFn: (): Promise<EnrollmentDetail> =>
      unwrap(
        api.GET("/api/v1/enrollments/{enrollment_id}", {
          params: { path: { enrollment_id: enrollmentId } },
        }),
      ),
  });

export const notesImagesQuery = (enrollmentId: string, lessonId: string) =>
  queryOptions({
    queryKey: ["learn", "notes-images", enrollmentId, lessonId] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/images", {
          params: { path: lessonPath(enrollmentId, lessonId) },
        }),
      ),
    // Signed URLs expire after a few minutes: refresh before they do.
    staleTime: 3 * 60_000,
    refetchInterval: 4 * 60_000,
  });

/** Record that the lesson was opened (drives "Continue learning" and resume). */
export function useVisitLesson(enrollmentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (lessonId: string) =>
      unwrap(
        api.POST("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/visit", {
          params: { path: lessonPath(enrollmentId, lessonId) },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: learnKeys.enrollments, exact: true }),
  });
}

export function useCompleteLesson(enrollmentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (lessonId: string) =>
      unwrap(
        api.POST("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/complete", {
          params: { path: lessonPath(enrollmentId, lessonId) },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: learnKeys.enrollments }),
  });
}

/** A short-lived signed URL for a pdf lesson; issuing it counts as opening the PDF. */
export function usePdfAccess(enrollmentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (lessonId: string) =>
      unwrap(
        api.POST("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/pdf-access", {
          params: { path: lessonPath(enrollmentId, lessonId) },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: learnKeys.enrollment(enrollmentId) }),
  });
}
