"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorAlert, LoadMore } from "@/features/admin/ui";

import { batchCoursesQuery } from "./api";

/** On a batch's page: the courses it has and how far its students are in each. */
export function BatchCourses({ batchId }: { batchId: string }) {
  const query = useInfiniteQuery(batchCoursesQuery(batchId));
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <section aria-labelledby="batch-courses-heading" className="flex flex-col gap-2">
      <h2 id="batch-courses-heading" className="text-base font-medium">
        Courses and progress
      </h2>
      {query.isPending ? <Skeleton className="h-16 w-full" /> : null}
      {query.error ? <ErrorAlert error={query.error} /> : null}
      {query.isSuccess && items.length === 0 ? (
        <EmptyState>No courses are assigned to this batch yet.</EmptyState>
      ) : null}
      <ul className="flex flex-col gap-2" aria-label="Batch courses">
        {items.map((course) => (
          <li key={course.course_id}>
            <Link
              href={`/teach/courses/${course.course_id}/progress?batch=${batchId}`}
              className="flex flex-col gap-2 rounded-lg border p-3 hover:bg-muted/50"
            >
              <span className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium">{course.title}</span>
                <span className="text-sm text-muted-foreground tabular-nums">
                  {course.completed} of {course.enrolled} completed · average{" "}
                  {course.average_percent}%
                </span>
              </span>
              <Progress
                value={course.average_percent}
                aria-label={`${course.title}: average progress ${course.average_percent}%`}
              />
            </Link>
          </li>
        ))}
      </ul>
      <LoadMore
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
    </section>
  );
}
