"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";

import { PageSkeleton, StatusBadge } from "@/components/patterns/states";
import { EmptyState, ErrorAlert, LoadMore } from "@/features/admin/ui";
import { dueSoonQuery, recentResultsQuery } from "@/features/reports/dashboard-api";
import { formatIst } from "@/lib/ist";

export function LearningActivity() {
  const due = useInfiniteQuery(dueSoonQuery());
  const results = useInfiniteQuery(recentResultsQuery());
  const assignments = due.data?.pages.flatMap((p) => p.items) ?? [];
  const outcomes = results.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <>
      <section className="flex flex-col gap-3" aria-label="Due soon">
        <h2>Due soon</h2>
        <p className="text-sm text-muted-foreground">Unsubmitted assignments due within 7 days.</p>
        {due.isPending ? <PageSkeleton rows={2} /> : null}
        {due.error ? <ErrorAlert error={due.error} onRetry={() => void due.refetch()} /> : null}
        {due.isSuccess && !assignments.length ? (
          <EmptyState href="/learn" actionLabel="Continue learning">
            No assignments due in the next 7 days.
          </EmptyState>
        ) : null}
        <ul className="flex flex-col gap-3">
          {assignments.map((a) => (
            <li key={`${a.enrollment_id}-${a.lesson_id}`} className="state-card">
              <p className="text-sm text-muted-foreground">{a.course_title}</p>
              <Link
                href={`/learn/enrollments/${a.enrollment_id}/lessons/${a.lesson_id}`}
                className="flex min-h-11 items-center justify-between gap-3 text-primary underline"
              >
                <span>{a.title}</span>
              </Link>
              <p className="text-sm text-warning">Due {formatIst(a.due_at)}</p>
            </li>
          ))}
        </ul>
        <LoadMore
          hasNextPage={due.hasNextPage}
          isFetchingNextPage={due.isFetchingNextPage}
          onClick={() => void due.fetchNextPage()}
        />
      </section>
      <section className="flex flex-col gap-3" aria-label="Recent results">
        <h2>Recent results</h2>
        {results.isPending ? <PageSkeleton rows={2} /> : null}
        {results.error ? (
          <ErrorAlert error={results.error} onRetry={() => void results.refetch()} />
        ) : null}
        {results.isSuccess && !outcomes.length ? (
          <EmptyState href="/learn" actionLabel="Continue learning">
            Your grades and submitted quiz results appear here.
          </EmptyState>
        ) : null}
        <ul className="flex flex-col gap-3">
          {outcomes.map((r) => (
            <li key={`${r.kind}-${r.id}`} className="state-card">
              <p className="text-sm text-muted-foreground">{r.course_title}</p>
              <Link
                href={`/learn/enrollments/${r.enrollment_id}/lessons/${r.lesson_id}`}
                className="inline-flex min-h-11 items-center text-primary underline"
              >
                {r.title}
              </Link>
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-semibold tabular-nums">
                  {Number(r.score)} / {Number(r.max_marks)}
                </span>
                <StatusBadge kind={r.kind === "assignment" || r.passed ? "success" : "danger"}>
                  {r.kind === "assignment" ? "Graded" : r.passed ? "Passed" : "Not passed"}
                </StatusBadge>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">{formatIst(r.occurred_at)}</p>
            </li>
          ))}
        </ul>
        <LoadMore
          hasNextPage={results.hasNextPage}
          isFetchingNextPage={results.isFetchingNextPage}
          onClick={() => void results.fetchNextPage()}
        />
      </section>
    </>
  );
}
