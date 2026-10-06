import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { unwrap } from "@/lib/api/unwrap";

type Page<T> = { items: T[]; next_cursor: string | null };
type S = components["schemas"];
function pages<T>(key: string, load: (cursor: string | undefined) => Promise<Page<T>>) {
  return infiniteQueryOptions({
    staleTime: 0,
    queryKey: ["dashboards", key] as const,
    queryFn: ({ pageParam }) => load(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page: Page<T>) => page.next_cursor ?? undefined,
  });
}

export const adminOverviewQuery = () =>
  queryOptions({
    staleTime: 0,
    queryKey: ["dashboards", "admin"],
    queryFn: () => unwrap(api.GET("/api/v1/dashboards/admin")),
  });
export const teachOverviewQuery = () =>
  queryOptions({
    staleTime: 0,
    queryKey: ["dashboards", "teach"],
    queryFn: () => unwrap(api.GET("/api/v1/dashboards/teach")),
  });
export const dashboardBatchesQuery = () =>
  pages<S["DashboardBatch"]>("batches", (cursor) =>
    unwrap(
      api.GET("/api/v1/dashboards/admin/batches", { params: { query: { limit: 20, cursor } } }),
    ),
  );
export const unassignedCoursesQuery = () =>
  pages<S["CourseOut"]>("unassigned", (cursor) =>
    unwrap(
      api.GET("/api/v1/dashboards/admin/unassigned-courses", {
        params: { query: { limit: 20, cursor } },
      }),
    ),
  );
export const gradingQueueQuery = () =>
  pages<S["CrossCourseSubmissionRow"]>("grading", (cursor) =>
    unwrap(api.GET("/api/v1/assignment-submissions", { params: { query: { limit: 20, cursor } } })),
  );
export const dueSoonQuery = () =>
  pages<S["DueAssignment"]>("due", (cursor) =>
    unwrap(api.GET("/api/v1/dashboards/learn/due", { params: { query: { limit: 10, cursor } } })),
  );
export const recentResultsQuery = () =>
  pages<S["LearningResult"]>("results", (cursor) =>
    unwrap(
      api.GET("/api/v1/dashboards/learn/results", { params: { query: { limit: 10, cursor } } }),
    ),
  );
