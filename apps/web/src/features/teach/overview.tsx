"use client";

import { useQuery, useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";

import { FirstRunChecklist, PageSkeleton, StatusBadge } from "@/components/patterns/states";
import { ErrorAlert, EmptyState, LoadMore, PageTitle } from "@/features/admin/ui";
import { gradingQueueQuery, teachOverviewQuery } from "@/features/reports/dashboard-api";
import { formatIst } from "@/lib/ist";

export function TeachingAttention({ onCreate }: { onCreate: () => void }) {
  const query = useQuery(teachOverviewQuery());
  const s = query.data;
  if (query.isPending) return <PageSkeleton rows={2} />;
  if (query.error) return <ErrorAlert error={query.error} onRetry={() => void query.refetch()} />;
  if (!s) return null;
  return (
    <>
      <FirstRunChecklist
        title="Publish your first course"
        steps={[
          {
            label: "Create a course",
            done: s.has_course,
            href: "/teach/courses",
            onAction: onCreate,
          },
          { label: "Add a lesson", done: s.has_lesson, href: "/teach/courses" },
          { label: "Publish", done: s.has_publication, href: "/teach/courses" },
        ]}
      />
      <section className="state-card flex flex-col gap-4" aria-label="Needs attention">
        <h2>Needs attention</h2>
        <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <div>
            <dt className="text-sm text-muted-foreground">Ungraded submissions</dt>
            <dd className="text-xl font-semibold tabular-nums">
              <Link
                className="inline-flex min-h-11 items-center text-primary underline"
                href="/teach/grading"
              >
                {s.ungraded_count}
              </Link>
            </dd>
            <dd className="text-sm text-muted-foreground">
              {s.oldest_ungraded_at
                ? `Oldest: ${formatIst(s.oldest_ungraded_at)}`
                : "Queue is clear"}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-muted-foreground">Assignments due within 7 days</dt>
            <dd className="text-xl font-semibold tabular-nums">{s.assignments_due_soon}</dd>
          </div>
          <div>
            <dt className="text-sm text-muted-foreground">Students inactive for 7+ days</dt>
            <dd className="text-xl font-semibold tabular-nums">{s.inactive_students}</dd>
          </div>
          <div>
            <dt className="text-sm text-muted-foreground">Quiz outcomes · last 7 days</dt>
            <dd className="flex flex-wrap gap-2">
              <StatusBadge kind="success">{s.quiz_passes} passed</StatusBadge>
              <StatusBadge kind="danger">{s.quiz_failures} failed</StatusBadge>
            </dd>
          </div>
        </dl>
      </section>
    </>
  );
}

export function CrossCourseGrading() {
  const query = useInfiniteQuery(gradingQueueQuery());
  const rows = query.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="flex flex-col gap-4">
      <PageTitle title="Grading" />
      <p className="text-muted-foreground">
        All your courses. Ungraded submissions first, oldest first.
      </p>
      {query.isPending ? <PageSkeleton kind="table" /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && !rows.length ? (
        <EmptyState href="/teach/courses" actionLabel="View courses">
          Nothing to grade.
        </EmptyState>
      ) : null}
      <ul className="flex flex-col gap-3" aria-label="Submissions">
        {rows.map((r) => (
          <li key={r.id}>
            <Link
              className="state-card flex min-h-11 flex-col gap-2 hover:bg-muted"
              href={`/teach/submissions/${r.id}?from=grading`}
            >
              <span className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold">{r.student.full_name || r.student.email}</span>
                <StatusBadge kind={r.status === "graded" ? "success" : "warning"}>
                  {r.status === "graded" ? `${Number(r.score)} / ${r.max_marks}` : "To grade"}
                </StatusBadge>
              </span>
              <span>
                {r.course_title} · {r.assignment_title}
              </span>
              <span className="text-sm text-muted-foreground">
                Submitted {formatIst(r.submitted_at)}
              </span>
            </Link>
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
