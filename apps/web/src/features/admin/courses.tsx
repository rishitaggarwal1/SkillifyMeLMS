"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { PageSkeleton } from "@/components/patterns/states";
import { coursesQuery } from "@/features/teach/api";
import { AssignmentsPanel } from "@/features/teach/assignments-panel";
import type { Course } from "@/lib/api/types";

import { EmptyState, ErrorAlert, LoadMore, PageTitle } from "./ui";

/**
 * /admin/courses: courses granted to the organization. The org admin chooses which batches
 * receive each one (narrowing the grant; only the publisher can widen it) and opens progress.
 */
export function AdminCoursesPage() {
  const query = useInfiniteQuery(coursesQuery(false));
  const courses = query.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="flex flex-col gap-4">
      <PageTitle title="Courses" />
      <p className="max-w-prose text-sm text-muted-foreground">
        Courses your organization has been granted. Choose which batches get each one; students see
        a course only once their batch has it.
      </p>
      {query.isPending ? <PageSkeleton /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && courses.length === 0 ? (
        <EmptyState>No courses have been granted to your organization yet.</EmptyState>
      ) : null}
      <ul className="flex flex-col gap-3" aria-label="Granted courses">
        {courses.map((course) => (
          <GrantedCourse key={course.id} course={course} />
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

function GrantedCourse({ course }: { course: Course }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="flex flex-col gap-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="min-w-0">
          <span className="block font-medium">{course.title}</span>
          {course.current_version ? (
            <Badge variant="outline">v{course.current_version.version}</Badge>
          ) : null}
        </span>
        <span className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Done" : "Choose batches"}
          </Button>
          <Link
            href={`/teach/courses/${course.id}/progress`}
            className={buttonVariants({ variant: "ghost", size: "sm" })}
          >
            Progress
          </Link>
        </span>
      </div>
      {open ? <AssignmentsPanel course={course} /> : null}
    </li>
  );
}
