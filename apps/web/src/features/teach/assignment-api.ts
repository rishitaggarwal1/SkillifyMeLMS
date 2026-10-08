import {
  infiniteQueryOptions,
  queryOptions,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import type { GraderSubmission, GraderSubmissionRow, Rubric } from "@/lib/api/types";
import type { components } from "@/lib/api/schema";
import { unwrap } from "@/lib/api/unwrap";

import { keys, useOutlineEdit, type IfMatch } from "./api";
import type { NotesDoc } from "./notes-doc";

type Page<T> = { items: T[]; next_cursor: string | null };
const lessonPath = (courseId: string, lessonId: string) => ({
  course_id: courseId,
  lesson_id: lessonId,
});

// ---------------------------------------------------------------------------- authoring

export const assignmentDraftQuery = (courseId: string, lessonId: string) =>
  queryOptions({
    queryKey: [...keys.lesson(courseId, lessonId), "assignment"] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/courses/{course_id}/lessons/{lesson_id}/assignment", {
          params: { path: lessonPath(courseId, lessonId) },
        }),
      ),
  });

export type AssignmentFields = {
  title: string;
  instructions: NotesDoc | null;
  due_at: string | null;
  max_marks: number;
  submission_kinds: ("file" | "text")[];
  rubric: Rubric | null;
  late_policy: components["schemas"]["LatePolicy-Input"] | null;
};

export const assignmentPreviewQuery = (courseId: string, lessonId: string) =>
  queryOptions({
    queryKey: [...keys.lesson(courseId, lessonId), "assignment-preview"] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/courses/{course_id}/lessons/{lesson_id}/assignment/preview", {
          params: { path: lessonPath(courseId, lessonId) },
        }),
      ),
    staleTime: 2 * 60_000,
  });

/** Save the definition: an outline edit (course revision as If-Match, serialized per course). */
export function useSaveAssignment(courseId: string, lessonId: string) {
  const qc = useQueryClient();
  return useOutlineEdit(
    courseId,
    (body: AssignmentFields, header: IfMatch) =>
      unwrap(
        api.PUT("/api/v1/courses/{course_id}/lessons/{lesson_id}/assignment", {
          params: { path: lessonPath(courseId, lessonId), header },
          body,
        }),
      ),
    {
      revisionOf: (saved) => saved.course_revision,
      onSuccess: (saved) =>
        qc.setQueryData(assignmentDraftQuery(courseId, lessonId).queryKey, saved),
    },
  );
}

// ---------------------------------------------------------------------------- grading

export const versionDetailQuery = (courseId: string, versionId: string) =>
  queryOptions({
    queryKey: [...keys.course(courseId), "versions", versionId] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/courses/{course_id}/versions/{version_id}", {
          params: { path: { course_id: courseId, version_id: versionId } },
        }),
      ),
  });

export type SubmissionFilters = { status?: "submitted" | "graded"; batchId?: string };

export const submissionsQuery = (courseId: string, lessonId: string, filters: SubmissionFilters) =>
  infiniteQueryOptions({
    queryKey: ["grading", courseId, lessonId, filters] as const,
    queryFn: ({ pageParam }): Promise<Page<GraderSubmissionRow>> =>
      unwrap(
        api.GET("/api/v1/courses/{course_id}/lessons/{lesson_id}/submissions", {
          params: {
            path: lessonPath(courseId, lessonId),
            query: {
              limit: 25,
              cursor: pageParam,
              status: filters.status,
              batch_id: filters.batchId,
            },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

export const submissionQuery = (submissionId: string) =>
  queryOptions({
    queryKey: ["grading", "submission", submissionId] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/assignment-submissions/{submission_id}", {
          params: { path: { submission_id: submissionId } },
        }),
      ),
    // The file link is a short-lived signed URL.
    staleTime: 2 * 60_000,
  });

export type GradeValues = {
  score?: string;
  criterion_scores?: { criterion_id: string; score: string }[];
  attempt_id?: string;
  feedback: string;
  revision: number;
};

export const attemptsQuery = (submissionId: string) =>
  infiniteQueryOptions({
    queryKey: ["grading", "submission", submissionId, "attempts"] as const,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/assignment-submissions/{submission_id}/attempts", {
          params: {
            path: { submission_id: submissionId },
            query: { limit: 25, cursor: pageParam },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

export const attemptQuery = (submissionId: string, attemptId: string) =>
  queryOptions({
    queryKey: ["grading", "submission", submissionId, "attempt", attemptId] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/assignment-submissions/{submission_id}/attempts/{attempt_id}", {
          params: { path: { submission_id: submissionId, attempt_id: attemptId } },
        }),
      ),
    staleTime: 2 * 60_000,
  });

export const gradeHistoryQuery = (submissionId: string, attemptId: string) =>
  infiniteQueryOptions({
    queryKey: ["grading", "submission", submissionId, "grades", attemptId] as const,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/assignment-submissions/{submission_id}/attempts/{attempt_id}/grades", {
          params: {
            path: { submission_id: submissionId, attempt_id: attemptId },
            query: { limit: 25, cursor: pageParam },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

export function useGrade(submissionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ revision, ...body }: GradeValues): Promise<GraderSubmission> =>
      unwrap(
        api.PUT("/api/v1/assignment-submissions/{submission_id}/grade", {
          params: {
            path: { submission_id: submissionId },
            header: { "If-Match": String(revision) },
          },
          body,
        }),
      ),
    onSuccess: (detail) => {
      qc.setQueryData(submissionQuery(submissionId).queryKey, detail);
      void qc.invalidateQueries({ queryKey: ["grading", "submission", submissionId] });
      void qc.invalidateQueries({ queryKey: ["dashboards"] });
      void qc.invalidateQueries({ queryKey: ["grading", detail.course_id, detail.lesson_id] });
    },
  });
}
