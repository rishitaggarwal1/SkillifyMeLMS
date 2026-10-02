"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorAlert } from "@/features/admin/ui";

import { myEnrollmentsQuery } from "./api";
import { splitDashboard, type Enrollment } from "./outline";

export function Dashboard() {
  const query = useQuery(myEnrollmentsQuery());
  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.error) return <ErrorAlert error={query.error} />;
  const { continueLearning, others } = splitDashboard(query.data);
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">My learning</h1>
      {query.data.length === 0 ? (
        <EmptyState>
          No courses yet. Courses appear here when your college assigns them to your batch.
        </EmptyState>
      ) : null}
      {continueLearning.length ? (
        <section aria-label="Continue learning" className="flex flex-col gap-3">
          <h2 className="text-base font-semibold">Continue learning</h2>
          <ul className="flex flex-col gap-2">
            {continueLearning.map((enrollment, index) => (
              <CourseCard key={enrollment.id} enrollment={enrollment} featured={index === 0} />
            ))}
          </ul>
        </section>
      ) : null}
      {others.length ? (
        <section aria-label="My courses" className="flex flex-col gap-3">
          <h2 className="text-base font-semibold">
            {continueLearning.length ? "More courses" : "My courses"}
          </h2>
          <ul className="flex flex-col gap-2">
            {others.map((enrollment) => (
              <CourseCard key={enrollment.id} enrollment={enrollment} />
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function CourseCard({
  enrollment,
  featured = false,
}: {
  enrollment: Enrollment;
  featured?: boolean;
}) {
  const percent = enrollment.progress_percent;
  const status = enrollment.completed_at
    ? "Completed"
    : enrollment.last_accessed_at
      ? "Continue"
      : "Start";
  return (
    <li>
      <Link
        href={`/learn/enrollments/${enrollment.id}`}
        className={`flex flex-col gap-2 rounded-lg border p-3 hover:bg-muted/50 ${featured ? "border-primary/40 bg-primary/5" : ""}`}
      >
        <span className="flex items-start justify-between gap-3">
          <span className="min-w-0 font-medium">{enrollment.course_title}</span>
          {enrollment.completed_at ? (
            <Badge variant="secondary">Completed</Badge>
          ) : (
            <span className="shrink-0 text-sm font-medium text-primary">{status} →</span>
          )}
        </span>
        <Progress value={percent} aria-label={`${enrollment.course_title} progress`} />
        <span className="text-xs text-muted-foreground tabular-nums">{percent}% complete</span>
      </Link>
    </li>
  );
}
