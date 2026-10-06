import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/unwrap";
import { postForm, safeFileName } from "@/lib/upload";

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

// ---------------------------------------------------------------------------- assignments

export const myAssignmentQuery = (enrollmentId: string, lessonId: string) =>
  queryOptions({
    queryKey: ["learn", "assignment", enrollmentId, lessonId] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/assignment", {
          params: { path: lessonPath(enrollmentId, lessonId) },
        }),
      ),
    // Shows the grade once an instructor records it; the file link is short-lived.
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });

export const SUBMISSION_TYPES = ["application/pdf", "image/png", "image/jpeg"] as const;
type SubmissionType = (typeof SUBMISSION_TYPES)[number];

export type SubmitInput =
  | { kind: "text"; text: string; revision: number }
  | { kind: "file"; file: File; revision: number; onProgress: (fraction: number) => void };

/** Submit (or replace) the student's work; files go straight to storage first. */
export function useSubmitAssignment(enrollmentId: string, lessonId: string) {
  const qc = useQueryClient();
  const path = lessonPath(enrollmentId, lessonId);
  return useMutation({
    mutationFn: async (input: SubmitInput) => {
      const header = { "If-Match": String(input.revision) };
      if (input.kind === "text") {
        return unwrap(
          api.PUT("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/submission", {
            params: { path, header },
            body: { submission: { kind: "text", text: input.text } },
          }),
        );
      }
      const type = input.file.type as SubmissionType;
      if (!(SUBMISSION_TYPES as readonly string[]).includes(type)) {
        throw new Error("Choose a PDF, PNG or JPEG file.");
      }
      const created = await unwrap(
        api.POST("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/submission-upload", {
          params: { path, header },
          body: { file_name: safeFileName(input.file.name, "submission"), content_type: type },
        }),
      );
      if (input.file.size > created.upload.max_bytes) {
        throw new Error(
          `That file is larger than ${Math.floor(created.upload.max_bytes / 1024 ** 2)} MB.`,
        );
      }
      await postForm(created.upload.url, created.upload.fields, input.file, input.onProgress);
      return unwrap(
        api.PUT("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/submission", {
          params: { path, header },
          body: { submission: { kind: "file", file_id: created.file.id } },
        }),
      );
    },
    onSettled: () =>
      qc.invalidateQueries({ queryKey: myAssignmentQuery(enrollmentId, lessonId).queryKey }),
  });
}
