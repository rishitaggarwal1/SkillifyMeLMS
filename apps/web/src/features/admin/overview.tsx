"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { DataTable } from "@/components/patterns/data-table";
import { FirstRunChecklist, PageSkeleton, StatusBadge } from "@/components/patterns/states";
import { Progress } from "@/components/ui/progress";
import {
  adminOverviewQuery,
  dashboardBatchesQuery,
  unassignedCoursesQuery,
} from "@/features/reports/dashboard-api";
import { formatIst } from "@/lib/ist";

import { EmptyState, ErrorAlert, LoadMore, PageTitle } from "./ui";

export function AdminOverview() {
  const summary = useQuery(adminOverviewQuery());
  const batches = useInfiniteQuery(dashboardBatchesQuery());
  const grants = useInfiniteQuery(unassignedCoursesQuery());
  const rows = batches.data?.pages.flatMap((p) => p.items) ?? [];
  const unassigned = grants.data?.pages.flatMap((p) => p.items) ?? [];
  const s = summary.data;
  return (
    <div className="flex flex-col gap-6">
      <PageTitle title="College overview" />
      {summary.isPending ? <PageSkeleton rows={3} /> : null}
      {summary.error ? (
        <ErrorAlert error={summary.error} onRetry={() => void summary.refetch()} />
      ) : null}
      {s ? (
        <>
          <FirstRunChecklist
            title="Set up your college"
            steps={[
              { label: "Create a batch", done: s.has_batch, href: "/admin/batches" },
              { label: "Import or invite students", done: s.has_students, href: "/admin/imports" },
              { label: "Assign a course", done: s.has_assignment, href: "/admin/courses" },
            ]}
          />
          <div className="grid gap-3 sm:grid-cols-3" aria-label="Organization activity">
            <Link className="state-card" href="/admin/members#invitations">
              <span className="block text-sm text-muted-foreground">Pending invitations</span>
              <span className="text-2xl font-semibold tabular-nums">{s.pending_invitations}</span>
            </Link>
            <Link className="state-card" href="/admin/imports">
              <span className="block text-sm text-muted-foreground">Running imports</span>
              <span className="text-2xl font-semibold tabular-nums">{s.running_imports}</span>
            </Link>
            <Link className="state-card" href="/admin/imports">
              <span className="block text-sm text-muted-foreground">Imports needing attention</span>
              <span className="text-2xl font-semibold tabular-nums">{s.failed_imports}</span>
            </Link>
          </div>
        </>
      ) : null}
      <section aria-label="Courses to assign" className="flex flex-col gap-3">
        <h2>Courses to assign</h2>
        <p className="text-sm text-muted-foreground">
          Granted courses become visible to students after you choose their batches.
        </p>
        {grants.isPending ? <PageSkeleton rows={2} /> : null}
        {grants.error ? (
          <ErrorAlert error={grants.error} onRetry={() => void grants.refetch()} />
        ) : null}
        {grants.isSuccess && unassigned.length === 0 ? (
          <EmptyState href="/admin/courses" actionLabel="View courses">
            Every granted course has a batch assignment.
          </EmptyState>
        ) : null}
        <ul className="grid gap-3" aria-label="Unassigned granted courses">
          {unassigned.map((c) => (
            <li key={c.id} className="state-card border-warning">
              <StatusBadge kind="warning">Choose batches</StatusBadge>
              <h3 className="mt-2 font-semibold">{c.title}</h3>
              <Link
                href={`/teach/courses/${c.id}`}
                className="inline-flex min-h-11 items-center text-primary underline"
              >
                Assign to batches
              </Link>
            </li>
          ))}
        </ul>
        <LoadMore
          hasNextPage={grants.hasNextPage}
          isFetchingNextPage={grants.isFetchingNextPage}
          onClick={() => void grants.fetchNextPage()}
        />
      </section>
      <section aria-label="Batch progress" className="flex flex-col gap-3">
        <h2>Batches</h2>
        <p className="text-sm text-muted-foreground">
          Completion is mean course progress across this batch&apos;s active enrollments.
        </p>
        {batches.isPending ? <PageSkeleton kind="table" /> : null}
        {batches.error ? (
          <ErrorAlert error={batches.error} onRetry={() => void batches.refetch()} />
        ) : null}
        {rows.length ? (
          <DataTable
            label="Batch progress"
            rows={rows}
            rowKey={(r) => r.id}
            columns={[
              {
                key: "name",
                title: "Batch",
                render: (r) => (
                  <Link
                    className="inline-flex min-h-11 items-center text-primary underline"
                    href={`/admin/batches/${r.id}`}
                  >
                    {r.name}
                  </Link>
                ),
              },
              {
                key: "progress",
                title: "Completion",
                render: (r) =>
                  r.completion_percent === null ? (
                    <span>No enrollments</span>
                  ) : (
                    <div className="min-w-28">
                      <span>{r.completion_percent}%</span>
                      <Progress value={r.completion_percent} aria-label={`${r.name} completion`} />
                    </div>
                  ),
              },
              {
                key: "activity",
                title: "Last activity",
                render: (r) =>
                  r.last_activity_at ? formatIst(r.last_activity_at) : "No activity yet",
              },
            ]}
          />
        ) : null}
        <LoadMore
          hasNextPage={batches.hasNextPage}
          isFetchingNextPage={batches.isFetchingNextPage}
          onClick={() => void batches.fetchNextPage()}
        />
      </section>
    </div>
  );
}
