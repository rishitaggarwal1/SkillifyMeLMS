import {
  infiniteQueryOptions,
  queryOptions,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import type {
  AuditEntry,
  Organization,
  OrgRole,
  PlatformCourse,
  PlatformUser,
} from "@/lib/api/types";
import { unwrap } from "@/lib/api/unwrap";

const PAGE_SIZE = 25;

type Page<T> = { items: T[]; next_cursor: string | null };
const nextCursor = <T>(last: Page<T>) => last.next_cursor ?? undefined;

// Platform screens read across every organization: their keys start with "platform" so an org
// switch (which invalidates everything) refetches them too, harmlessly.

export const summaryQuery = () =>
  queryOptions({
    queryKey: ["platform", "summary"] as const,
    queryFn: () => unwrap(api.GET("/api/v1/platform/summary")),
  });

// ---------------------------------------------------------------------------- organizations

export type OrgFilters = { q?: string; status?: "active" | "archived" };

export const organizationsQuery = (filters: OrgFilters) =>
  infiniteQueryOptions({
    queryKey: ["platform", "organizations", filters] as const,
    queryFn: ({ pageParam }): Promise<Page<Organization>> =>
      unwrap(
        api.GET("/api/v1/organizations", {
          params: {
            query: {
              limit: PAGE_SIZE,
              cursor: pageParam,
              q: filters.q || undefined,
              status: filters.status,
            },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

export const organizationQuery = (organizationId: string) =>
  queryOptions({
    queryKey: ["platform", "organizations", organizationId] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/organizations/{organization_id}", {
          params: { path: { organization_id: organizationId } },
        }),
      ),
  });

export function useCreateOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; slug: string; is_content_publisher: boolean }) =>
      unwrap(api.POST("/api/v1/organizations", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["platform"] }),
  });
}

export function useUpdateOrganization(organizationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name?: string;
      is_content_publisher?: boolean;
      status?: "active" | "archived";
    }) =>
      unwrap(
        api.PATCH("/api/v1/organizations/{organization_id}", {
          params: { path: { organization_id: organizationId } },
          body,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["platform"] }),
  });
}

export function useInviteOrgAdmin(organizationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; full_name: string }) =>
      unwrap(
        api.POST("/api/v1/platform/organizations/{organization_id}/admins", {
          params: { path: { organization_id: organizationId } },
          body,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["platform"] }),
  });
}

// ---------------------------------------------------------------------------- users

export type UserFilters = {
  q?: string;
  role?: OrgRole;
  organizationId?: string;
  status?: "invited" | "active" | "disabled";
};

export const usersQuery = (filters: UserFilters) =>
  infiniteQueryOptions({
    queryKey: ["platform", "users", filters] as const,
    queryFn: ({ pageParam }): Promise<Page<PlatformUser>> =>
      unwrap(
        api.GET("/api/v1/platform/users", {
          params: {
            query: {
              limit: PAGE_SIZE,
              cursor: pageParam,
              q: filters.q || undefined,
              role: filters.role,
              organization_id: filters.organizationId,
              status: filters.status,
            },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

export const userQuery = (userId: string) =>
  queryOptions({
    queryKey: ["platform", "users", userId] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/platform/users/{user_id}", { params: { path: { user_id: userId } } }),
      ),
  });

export function useSetUserEnabled(userId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      unwrap(
        enabled
          ? api.POST("/api/v1/platform/users/{user_id}/enable", {
              params: { path: { user_id: userId } },
            })
          : api.POST("/api/v1/platform/users/{user_id}/disable", {
              params: { path: { user_id: userId } },
            }),
      ),
    onSuccess: (user) => {
      qc.setQueryData(userQuery(userId).queryKey, user);
      void qc.invalidateQueries({ queryKey: ["platform"] });
    },
  });
}

// ---------------------------------------------------------------------------- courses

export type CourseFilters = { organizationId?: string; status?: "active" | "archived" };

export const coursesQuery = (filters: CourseFilters) =>
  infiniteQueryOptions({
    queryKey: ["platform", "courses", filters] as const,
    queryFn: ({ pageParam }): Promise<Page<PlatformCourse>> =>
      unwrap(
        api.GET("/api/v1/platform/courses", {
          params: {
            query: {
              limit: PAGE_SIZE,
              cursor: pageParam,
              organization_id: filters.organizationId,
              status: filters.status,
            },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

// ---------------------------------------------------------------------------- audit log

export type AuditFilters = {
  organizationId?: string;
  action?: string;
  actorUserId?: string;
  targetType?: string;
  /** ISO dates (yyyy-mm-dd), read as whole days in India time. */
  from?: string;
  to?: string;
};

/** A calendar day in India (UTC+05:30, no DST) as the instant it starts. */
export function istDayStart(day: string): string {
  return new Date(`${day}T00:00:00+05:30`).toISOString();
}

export function istDayEnd(day: string): string {
  return new Date(new Date(`${day}T00:00:00+05:30`).getTime() + 86_400_000).toISOString();
}

export const auditQuery = (filters: AuditFilters) =>
  infiniteQueryOptions({
    queryKey: ["platform", "audit", filters] as const,
    queryFn: ({ pageParam }): Promise<Page<AuditEntry>> =>
      unwrap(
        api.GET("/api/v1/platform/audit-log", {
          params: {
            query: {
              limit: PAGE_SIZE,
              cursor: pageParam,
              organization_id: filters.organizationId,
              action: filters.action || undefined,
              actor_user_id: filters.actorUserId,
              target_type: filters.targetType || undefined,
              since: filters.from ? istDayStart(filters.from) : undefined,
              until: filters.to ? istDayEnd(filters.to) : undefined,
            },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });
