"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { NativeSelect } from "@/components/native-select";
import { Badge } from "@/components/ui/badge";
import { PageSkeleton } from "@/components/patterns/states";
import { EmptyState, ErrorAlert, LoadMore, PageTitle } from "@/features/admin/ui";

import { coursesQuery, organizationQuery } from "./api";

/** Every organization's courses, read-only (platform admins never edit content here). */
export function PlatformCoursesPage({ organizationId }: { organizationId?: string }) {
  const [status, setStatus] = useState<"" | "active" | "archived">("active");
  const query = useInfiniteQuery(coursesQuery({ organizationId, status: status || undefined }));
  const org = useQuery({ ...organizationQuery(organizationId ?? ""), enabled: !!organizationId });
  const rows = query.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex flex-col gap-4">
      <PageTitle title="Courses" />
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        {organizationId ? (
          <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span>
              Owned by <span className="font-medium text-foreground">{org.data?.name ?? "…"}</span>
            </span>
            <Link href="/platform/courses" className="underline-offset-4 hover:underline">
              All organizations
            </Link>
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">All organizations, read-only.</p>
        )}
        <NativeSelect
          aria-label="Filter by status"
          className="sm:w-40"
          value={status}
          onChange={(e) => setStatus(e.target.value as "" | "active" | "archived")}
        >
          <option value="active">Active</option>
          <option value="archived">Archived</option>
          <option value="">All</option>
        </NativeSelect>
      </div>
      {query.isPending ? <PageSkeleton /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && rows.length === 0 ? <EmptyState>No courses match.</EmptyState> : null}
      <ul className="flex flex-col gap-2" aria-label="Courses">
        {rows.map((c) => (
          <li key={c.id} className="flex flex-col gap-1 rounded-lg border p-3">
            <span className="flex items-start justify-between gap-2">
              <span className="min-w-0 font-medium">{c.title}</span>
              <span className="flex shrink-0 gap-1">
                {c.current_version ? (
                  <Badge variant="secondary">v{c.current_version.version}</Badge>
                ) : (
                  <Badge variant="outline">Draft</Badge>
                )}
                {c.status === "archived" ? <Badge variant="outline">Archived</Badge> : null}
              </span>
            </span>
            <span className="text-sm text-muted-foreground">
              <Link
                href={`/platform/organizations/${c.owner.id}`}
                className="underline-offset-4 hover:underline"
              >
                {c.owner.name}
              </Link>
              <span aria-hidden> · </span>
              {c.org_grant_count} {c.org_grant_count === 1 ? "organization" : "organizations"}{" "}
              granted · {c.batch_assignment_count}{" "}
              {c.batch_assignment_count === 1 ? "batch" : "batches"}
            </span>
          </li>
        ))}
      </ul>
      <LoadMore
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
    </div>
  );
}
