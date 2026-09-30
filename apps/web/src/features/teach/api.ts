import {
  infiniteQueryOptions,
  queryOptions,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type {
  Assignment,
  Course,
  CourseVersion,
  DirectoryOrganization,
  Draft,
  Skill,
  Video,
} from "@/lib/api/types";
import { unwrap } from "@/lib/api/unwrap";

const PAGE_SIZE = 25;
type Page<T> = { items: T[]; next_cursor: string | null };
const nextCursor = <T>(last: Page<T>) => last.next_cursor ?? undefined;
const path = (courseId: string) => ({ course_id: courseId });

// ---------------------------------------------------------------------------- query keys

export const keys = {
  courses: ["teach", "courses"] as const,
  course: (id: string) => ["teach", "courses", id] as const,
  draft: (id: string) => ["teach", "courses", id, "draft"] as const,
  lesson: (id: string, lessonId: string) => ["teach", "courses", id, "lessons", lessonId] as const,
};

// ---------------------------------------------------------------------------- reads

export const coursesQuery = (owned: boolean) =>
  infiniteQueryOptions({
    queryKey: [...keys.courses, { owned }] as const,
    queryFn: ({ pageParam }): Promise<Page<Course>> =>
      unwrap(
        api.GET("/api/v1/courses", {
          params: { query: { owned, limit: PAGE_SIZE, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

export const courseQuery = (courseId: string) =>
  queryOptions({
    queryKey: keys.course(courseId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/courses/{course_id}", { params: { path: path(courseId) } })),
  });

/** The editable draft. Its `course.revision` is what every outline edit sends as If-Match. */
export const draftQuery = (courseId: string) =>
  queryOptions({
    queryKey: keys.draft(courseId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/courses/{course_id}/draft", { params: { path: path(courseId) } })),
  });

export const lessonQuery = (courseId: string, lessonId: string) =>
  queryOptions({
    queryKey: keys.lesson(courseId, lessonId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/courses/{course_id}/lessons/{lesson_id}", {
          params: { path: { course_id: courseId, lesson_id: lessonId } },
        }),
      ),
  });

export const publishPreviewQuery = (courseId: string) =>
  queryOptions({
    queryKey: [...keys.course(courseId), "publish-preview"] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/courses/{course_id}/publish-preview", {
          params: { path: path(courseId) },
        }),
      ),
  });

export const versionsQuery = (courseId: string) =>
  queryOptions({
    queryKey: [...keys.course(courseId), "versions"] as const,
    queryFn: (): Promise<Page<CourseVersion>> =>
      unwrap(
        api.GET("/api/v1/courses/{course_id}/versions", {
          params: { path: path(courseId), query: { limit: 10 } },
        }),
      ),
  });

export const assignmentsQuery = (courseId: string) =>
  infiniteQueryOptions({
    queryKey: [...keys.course(courseId), "assignments"] as const,
    queryFn: ({ pageParam }): Promise<Page<Assignment>> =>
      unwrap(
        api.GET("/api/v1/courses/{course_id}/assignments", {
          params: { path: path(courseId), query: { limit: 100, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

export const skillsSearchQuery = (q: string) =>
  queryOptions({
    queryKey: ["skills", { q }] as const,
    queryFn: (): Promise<Page<Skill>> =>
      unwrap(api.GET("/api/v1/skills", { params: { query: { q: q || undefined, limit: 20 } } })),
    staleTime: 5 * 60_000,
  });

/** The whole (global, small) skills taxonomy, cached: names for skills a lesson is tagged with. */
export const allSkillsQuery = () =>
  queryOptions({
    queryKey: ["skills", "all"] as const,
    staleTime: 10 * 60_000,
    queryFn: async () => {
      const all = new Map<string, Skill>();
      let cursor: string | undefined;
      for (let page = 0; page < 20; page++) {
        const result: Page<Skill> = await unwrap(
          api.GET("/api/v1/skills", { params: { query: { limit: 100, cursor } } }),
        );
        for (const skill of result.items) all.set(skill.id, skill);
        if (!result.next_cursor) break;
        cursor = result.next_cursor;
      }
      return all;
    },
  });

export const readyVideosQuery = () =>
  infiniteQueryOptions({
    queryKey: ["videos", { status: "ready" }] as const,
    queryFn: ({ pageParam }): Promise<Page<Video>> =>
      unwrap(
        api.GET("/api/v1/videos", {
          params: { query: { status: "ready", limit: PAGE_SIZE, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

export const directoryQuery = (q: string) =>
  queryOptions({
    queryKey: ["organization-directory", { q }] as const,
    queryFn: (): Promise<Page<DirectoryOrganization>> =>
      unwrap(
        api.GET("/api/v1/organizations/directory", {
          params: { query: { q: q || undefined, limit: 20 } },
        }),
      ),
  });

export const directoryNamesQuery = (ids: string[]) =>
  queryOptions({
    queryKey: ["organization-directory", { ids }] as const,
    enabled: ids.length > 0,
    queryFn: async () => {
      const page: Page<DirectoryOrganization> = await unwrap(
        api.GET("/api/v1/organizations/directory", {
          params: { query: { ids, limit: 100 } },
        }),
      );
      return new Map(page.items.map((org) => [org.id, org.name]));
    },
  });

// ---------------------------------------------------------------------------- outline edits

export type IfMatch = { "If-Match": string };

/**
 * An edit of the course outline or a lesson. Every such request must carry the draft revision it
 * was based on (If-Match): the API answers 409 when someone else changed the course meanwhile.
 *
 * - Edits of one course run one at a time (mutation scope), each with the revision the previous
 *   one returned, so a burst of quick edits never trips over its own revisions.
 * - `optimistic` applies the change to the cached draft at once; on any error it is rolled back.
 * - On 409 the latest draft is refetched and the author is told why their change was undone.
 */
export function useOutlineEdit<V, R>(
  courseId: string,
  run: (vars: V, ifMatch: IfMatch) => Promise<R>,
  options: {
    revisionOf: (result: R) => number | Draft;
    optimistic?: (draft: Draft, vars: V) => Draft;
    onSuccess?: (result: R, vars: V) => void;
  },
) {
  const qc = useQueryClient();
  const key = keys.draft(courseId);
  return useMutation({
    scope: { id: `outline:${courseId}` },
    mutationFn: async (vars: V) => {
      const draft = qc.getQueryData<Draft>(key) ?? (await qc.fetchQuery(draftQuery(courseId)));
      return run(vars, { "If-Match": String(draft.course.revision) });
    },
    onMutate: async (vars: V) => {
      // Stop an older refetch from overwriting the revision this edit is about to produce.
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<Draft>(key);
      if (previous && options.optimistic) qc.setQueryData(key, options.optimistic(previous, vars));
      return { previous };
    },
    onSuccess: (result, vars) => {
      const next = options.revisionOf(result);
      qc.setQueryData<Draft>(key, (draft) => {
        if (typeof next !== "number") return next;
        return draft ? { ...draft, course: { ...draft.course, revision: next } } : draft;
      });
      options.onSuccess?.(result, vars);
    },
    onError: (error, _vars, context) => {
      if (context?.previous) qc.setQueryData(key, context.previous);
      if (error instanceof ApiError && error.status === 409 && error.code === "revision_conflict") {
        toast.error("This course was changed elsewhere. Showing the latest version; try again.");
      } else {
        toast.error(error instanceof Error ? error.message : "Could not save the change.");
      }
    },
    onSettled: () => qc.invalidateQueries({ queryKey: keys.course(courseId) }),
  });
}

export function useCreateCourse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { title: string; description: string; is_public_catalog: boolean }) =>
      unwrap(api.POST("/api/v1/courses", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.courses }),
  });
}

export function usePublish(courseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { release_type: "major" | "minor"; release_notes: string }) =>
      unwrap(
        api.POST("/api/v1/courses/{course_id}/versions", {
          params: { path: path(courseId) },
          body,
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.course(courseId) });
      void qc.invalidateQueries({ queryKey: keys.courses });
    },
  });
}

export function useCreateAssignments(courseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { organization_id?: string; batch_ids: string[] }) =>
      unwrap(
        api.POST("/api/v1/courses/{course_id}/assignments", {
          params: { path: path(courseId) },
          body,
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: [...keys.course(courseId), "assignments"] }),
  });
}

export function useDeleteAssignment(courseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (assignmentId: string) =>
      unwrap(
        api.DELETE("/api/v1/course-assignments/{assignment_id}", {
          params: { path: { assignment_id: assignmentId } },
        }),
      ),
    onSettled: () => qc.invalidateQueries({ queryKey: [...keys.course(courseId), "assignments"] }),
  });
}
