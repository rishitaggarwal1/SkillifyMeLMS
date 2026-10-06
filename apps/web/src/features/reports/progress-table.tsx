"use client";

import { useInfiniteQuery } from "@tanstack/react-query";

import { useState } from "react";
import { toast } from "sonner";

import { DataTable } from "@/components/patterns/data-table";
import { Button } from "@/components/ui/button";
import { PageSkeleton } from "@/components/patterns/states";
import { EmptyState, ErrorAlert, LoadMore, errorMessage } from "@/features/admin/ui";
import { toApiError } from "@/lib/api/errors";
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

/** The table: one row per student, a sticky name column, one column per lesson. Uses the shared sticky-header table and readable phone cards. */
export function ProgressGrid({
  lessons,
  rows,
}: {
  lessons: ProgressLesson[];
  rows: StudentProgress[];
}) {
  return (
    <DataTable
      label="Progress table"
      rows={rows}
      rowKey={(row) => row.student.id}
      columns={[
        {
          key: "student",
          title: "Student",
          rowHeader: true,
          render: (row) => (
            <>
              <span className="block font-medium">
                {row.student.full_name || row.student.email}
              </span>
              <span className="block text-xs text-muted-foreground">
                {row.enrollment_status === "active"
                  ? row.student.email
                  : row.enrollment_status === "revoked"
                    ? "Access removed"
                    : "Not enrolled"}
              </span>
            </>
          ),
        },
        {
          key: "percent",
          title: "Progress",
          render: (row) => <span className="tabular-nums">{row.progress_percent}%</span>,
        },
        {
          key: "activity",
          title: "Last activity",
          render: (row) => (row.last_activity_at ? formatIst(row.last_activity_at) : "Never"),
        },
        ...lessons.map((lesson) => ({
          key: lesson.id,
          title: lesson.title,
          headingTitle: `${lesson.module_title} / ${lesson.title}`,
          render: (row: StudentProgress) => <LessonCell row={row} lesson={lesson} />,
        })),
      ]}
    />
  );
}

/** Download the CSV, or say why not (e.g. 422 export_too_large) instead of saving the error
 * as a file. */
function CsvButton({ courseId, batchId }: { courseId: string; batchId: string }) {
  const [busy, setBusy] = useState(false);
  async function download() {
    setBusy(true);
    try {
      const response = await fetch(progressCsvUrl(courseId, batchId));
      if (!response.ok) {
        throw toApiError(response, await response.json().catch(() => null));
      }
      const name = /filename="([^"]+)"/.exec(
        response.headers.get("content-disposition") ?? "",
      )?.[1];
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = name ?? "progress.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Button variant="outline" size="sm" disabled={busy} onClick={() => void download()}>
      {busy ? "Preparing…" : "Download CSV"}
    </Button>
  );
}

/** A batch's progress in a course, with paging and the CSV download. */
export function CourseProgress({ courseId, batchId }: { courseId: string; batchId: string }) {
  const query = useInfiniteQuery(courseProgressQuery(courseId, batchId));
  const pages = query.data?.pages ?? [];
  const rows = pages.flatMap((p) => p.items);
  const first = pages[0];
  if (query.isPending) return <PageSkeleton />;
  if (query.error) return <ErrorAlert error={query.error} onRetry={() => void query.refetch()} />;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {first?.version ? `Columns: lessons of v${first.version}.` : "Not published yet."} ✓
          completed · ◐ started · · not started
        </p>
        <CsvButton courseId={courseId} batchId={batchId} />
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
