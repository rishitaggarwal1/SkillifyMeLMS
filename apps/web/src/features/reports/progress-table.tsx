"use client";

import { useInfiniteQuery } from "@tanstack/react-query";

import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorAlert, LoadMore } from "@/features/admin/ui";
import type { ProgressLesson, StudentProgress } from "@/lib/api/types";
import { formatIst } from "@/lib/ist";
import { cn } from "@/lib/utils";

import { courseProgressQuery, progressCsvUrl } from "./api";

const CELL: Record<string, { mark: string; label: string }> = {
  completed: { mark: "✓", label: "Completed" },
  in_progress: { mark: "◐", label: "Started" },
  not_started: { mark: "·", label: "Not started" },
  not_in_version: { mark: "n/a", label: "Not in this student's version" },
};

function LessonCell({ row, lesson }: { row: StudentProgress; lesson: ProgressLesson }) {
  const assignment = row.assignments[lesson.id];
  if (lesson.lesson_type === "assignment" && assignment) {
    const text =
      assignment.status === "graded"
        ? `${Number(assignment.score)}/${assignment.max_marks}`
        : "Submitted";
    return (
      <span className={assignment.status === "graded" ? "font-medium text-primary" : ""}>
        {text}
      </span>
    );
  }
  const cell = CELL[row.lessons[lesson.id] ?? "not_started"] ?? CELL.not_started!;
  return (
    <span
      title={cell.label}
      className={cn(cell.mark === "✓" ? "font-semibold text-primary" : "text-muted-foreground")}
    >
      {cell.mark}
      <span className="sr-only"> {cell.label}</span>
    </span>
  );
}

/** The table: one row per student, a sticky name column, one column per lesson. Scrolls
 * sideways on phones; nothing else on the page does. */
export function ProgressGrid({
  lessons,
  rows,
}: {
  lessons: ProgressLesson[];
  rows: StudentProgress[];
}) {
  return (
    <div
      className="relative max-w-full overflow-x-auto rounded-lg border"
      role="region"
      aria-label="Progress table"
      tabIndex={0}
    >
      <table className="w-max min-w-full border-collapse text-sm">
        <thead className="bg-muted/50 text-left">
          <tr>
            <th scope="col" className="sticky left-0 z-10 min-w-40 bg-muted px-3 py-2 font-medium">
              Student
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Progress
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Last activity
            </th>
            {lessons.map((lesson) => (
              <th
                key={lesson.id}
                scope="col"
                title={`${lesson.module_title} / ${lesson.title}`}
                className="max-w-28 truncate px-3 py-2 text-center font-medium"
              >
                {lesson.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.student.id} className="border-t">
              <th
                scope="row"
                className="sticky left-0 z-10 max-w-48 bg-background px-3 py-2 text-left font-normal"
              >
                <span className="block truncate font-medium">
                  {row.student.full_name || row.student.email}
                </span>
                <span className="block truncate text-xs text-muted-foreground">
                  {row.enrollment_status === "active"
                    ? row.student.email
                    : row.enrollment_status === "revoked"
                      ? "Access removed"
                      : "Not enrolled"}
                </span>
              </th>
              <td className="px-3 py-2 tabular-nums">{row.progress_percent}%</td>
              <td className="px-3 py-2 whitespace-nowrap text-muted-foreground">
                {row.last_activity_at ? formatIst(row.last_activity_at) : "Never"}
              </td>
              {lessons.map((lesson) => (
                <td key={lesson.id} className="px-3 py-2 text-center tabular-nums">
                  <LessonCell row={row} lesson={lesson} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A batch's progress in a course, with paging and the CSV download. */
export function CourseProgress({ courseId, batchId }: { courseId: string; batchId: string }) {
  const query = useInfiniteQuery(courseProgressQuery(courseId, batchId));
  const pages = query.data?.pages ?? [];
  const rows = pages.flatMap((p) => p.items);
  const first = pages[0];
  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.error) return <ErrorAlert error={query.error} />;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {first?.version ? `Columns: lessons of v${first.version}.` : "Not published yet."} ✓
          completed · ◐ started · · not started
        </p>
        <a
          href={progressCsvUrl(courseId, batchId)}
          className={buttonVariants({ variant: "outline", size: "sm" })}
          download
        >
          Download CSV
        </a>
      </div>
      {rows.length === 0 ? (
        <EmptyState>No students in this batch yet.</EmptyState>
      ) : (
        <ProgressGrid lessons={first?.lessons ?? []} rows={rows} />
      )}
      <LoadMore
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
    </div>
  );
}
