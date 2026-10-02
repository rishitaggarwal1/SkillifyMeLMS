import {
  infiniteQueryOptions,
  queryOptions,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import type { Batch, ImportJob, Invitation, Member, OrgRole } from "@/lib/api/types";
import { allPages } from "@/lib/api/all-pages";
import { unwrap } from "@/lib/api/unwrap";

const PAGE_SIZE = 25;

type Page<T> = { items: T[]; next_cursor: string | null };
const nextCursor = <T>(last: Page<T>) => last.next_cursor ?? undefined;

// ---------------------------------------------------------------------------- batches

export const batchesQuery = (status?: "active" | "archived") =>
  infiniteQueryOptions({
    queryKey: ["batches", { status }] as const,
    queryFn: ({ pageParam }): Promise<Page<Batch>> =>
      unwrap(
        api.GET("/api/v1/batches", {
          params: { query: { limit: PAGE_SIZE, cursor: pageParam, status } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

/** Every batch, for pickers (a dropdown can't load more): all pages, up to 2,000. */
export const allBatchesQuery = (status?: "active" | "archived") =>
  queryOptions({
    queryKey: ["batches", "all", { status }] as const,
    queryFn: () =>
      allPages((cursor): Promise<Page<Batch>> =>
        unwrap(api.GET("/api/v1/batches", { params: { query: { limit: 100, cursor, status } } })),
      ),
  });

export const batchQuery = (batchId: string) =>
  queryOptions({
    queryKey: ["batches", batchId] as const,
    queryFn: () =>
      unwrap(api.GET("/api/v1/batches/{batch_id}", { params: { path: { batch_id: batchId } } })),
  });

export const batchMembersQuery = (batchId: string) =>
  infiniteQueryOptions({
    queryKey: ["batches", batchId, "members"] as const,
    queryFn: ({ pageParam }): Promise<Page<Member>> =>
      unwrap(
        api.GET("/api/v1/batches/{batch_id}/members", {
          params: { path: { batch_id: batchId }, query: { limit: PAGE_SIZE, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

export function useCreateBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description: string }) =>
      unwrap(api.POST("/api/v1/batches", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["batches"] }),
  });
}

export function useArchiveBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (batchId: string) =>
      unwrap(api.DELETE("/api/v1/batches/{batch_id}", { params: { path: { batch_id: batchId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["batches"] }),
  });
}

export function useAddBatchMembers(batchId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userIds: string[]) =>
      unwrap(
        api.POST("/api/v1/batches/{batch_id}/members", {
          params: { path: { batch_id: batchId } },
          body: { user_ids: userIds },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["batches"] });
      void qc.invalidateQueries({ queryKey: ["members"] });
    },
  });
}

export function useRemoveBatchMember(batchId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      unwrap(
        api.DELETE("/api/v1/batches/{batch_id}/members/{user_id}", {
          params: { path: { batch_id: batchId, user_id: userId } },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["batches"] });
      void qc.invalidateQueries({ queryKey: ["members"] });
    },
  });
}

// ---------------------------------------------------------------------------- members

export type MemberFilters = { q?: string; role?: OrgRole; batchId?: string };

export const membersQuery = (filters: MemberFilters) =>
  infiniteQueryOptions({
    queryKey: ["members", filters] as const,
    queryFn: ({ pageParam }): Promise<Page<Member>> =>
      unwrap(
        api.GET("/api/v1/members", {
          params: {
            query: {
              limit: PAGE_SIZE,
              cursor: pageParam,
              q: filters.q || undefined,
              role: filters.role,
              batch_id: filters.batchId,
            },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

export function useUpdateMemberRoles() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, roles }: { userId: string; roles: OrgRole[] }) =>
      unwrap(
        api.PATCH("/api/v1/members/{user_id}", {
          params: { path: { user_id: userId } },
          body: { roles },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members"] }),
  });
}

export function useRemoveMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      unwrap(api.DELETE("/api/v1/members/{user_id}", { params: { path: { user_id: userId } } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["members"] });
      void qc.invalidateQueries({ queryKey: ["batches"] });
    },
  });
}

// ---------------------------------------------------------------------------- invitations

export const invitationsQuery = (status: "pending" | "accepted" | "revoked" | "expired") =>
  infiniteQueryOptions({
    queryKey: ["invitations", status] as const,
    queryFn: ({ pageParam }): Promise<Page<Invitation>> =>
      unwrap(
        api.GET("/api/v1/invitations", {
          params: { query: { limit: PAGE_SIZE, cursor: pageParam, status } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

export function useInvite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      email: string;
      full_name: string;
      roles: OrgRole[];
      batch_ids: string[];
    }) => unwrap(api.POST("/api/v1/invitations", { body })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["invitations"] });
      void qc.invalidateQueries({ queryKey: ["members"] });
    },
  });
}

export function useRevokeInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.DELETE("/api/v1/invitations/{invitation_id}", {
          params: { path: { invitation_id: id } },
        }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["invitations"] });
      void qc.invalidateQueries({ queryKey: ["members"] });
    },
  });
}

export function useResendInvitation() {
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/api/v1/invitations/{invitation_id}/resend", {
          params: { path: { invitation_id: id } },
        }),
      ),
  });
}

// ---------------------------------------------------------------------------- imports

export const FINISHED_IMPORT_STATUSES = new Set(["succeeded", "completed_with_errors", "failed"]);

export const importsQuery = () =>
  infiniteQueryOptions({
    queryKey: ["imports"] as const,
    queryFn: ({ pageParam }): Promise<Page<ImportJob>> =>
      unwrap(api.GET("/api/v1/imports", { params: { query: { limit: 10, cursor: pageParam } } })),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: nextCursor,
  });

export const importQuery = (jobId: string) =>
  queryOptions({
    queryKey: ["imports", jobId] as const,
    queryFn: () =>
      unwrap(api.GET("/api/v1/imports/{job_id}", { params: { path: { job_id: jobId } } })),
    // Poll while the background job runs; stop once it has finished.
    refetchInterval: (query) =>
      query.state.data && FINISHED_IMPORT_STATUSES.has(query.state.data.status) ? false : 1000,
  });

export function importErrorsUrl(jobId: string): string {
  return `/backend/api/v1/imports/${jobId}/errors.csv`;
}
