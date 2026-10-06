"use client";

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { NativeSelect } from "@/components/native-select";
import { Badge } from "@/components/ui/badge";
import { PageSkeleton } from "@/components/patterns/states";
import { allBatchesQuery } from "@/features/admin/api";
import { EmptyState, ErrorAlert, LoadMore, PageTitle } from "@/features/admin/ui";
import { hasPermission, useMe } from "@/features/auth/queries";
import { readOutline, type OutlineLesson } from "@/features/learn/outline";
import type { Course, GraderSubmissionRow } from "@/lib/api/types";
import { formatIst } from "@/lib/ist";

import { courseQuery } from "./api";
import {
  submissionQuery,
  submissionsQuery,
  useGrade,
  versionDetailQuery,
  type SubmissionFilters,
} from "./assignment-api";
import { GradeForm } from "./grade-form";

/** Assignment lessons of the course's current version, for graders (course page section). */
export function GradingSection({ course }: { course: Course }) {
  const { data: me } = useMe();
  const versionId = course.current_version?.id;
  const version = useQuery({
    ...versionDetailQuery(course.id, versionId ?? ""),
    enabled: !!versionId && hasPermission(me, "assignment.grade"),
  });
  if (!versionId || !hasPermission(me, "assignment.grade") || !version.data) return null;
  const lessons = readOutline(version.data.snapshot)
    .modules.flatMap((m) => m.lessons)
    .filter((l) => l.lesson_type === "assignment");
  if (lessons.length === 0) return null;
  return (
    <section aria-labelledby="grading-heading" className="flex flex-col gap-2">
      <h2 id="grading-heading" className="text-base font-semibold">
        Assignments to grade
      </h2>
      <p className="text-sm text-muted-foreground">
        Your organization&apos;s students&apos; submissions (v{course.current_version?.version}).
      </p>
      <ul className="flex flex-col gap-2" aria-label="Assignments to grade">
        {lessons.map((lesson) => (
          <li key={lesson.id}>
            <Link
              href={`/teach/courses/${course.id}/assignments/${lesson.id}`}
              className="flex items-center justify-between gap-3 rounded-lg border p-3 hover:bg-muted/50"
            >
              <span className="min-w-0 truncate font-medium">{assignmentTitle(lesson)}</span>
              <span className="shrink-0 text-sm text-muted-foreground">Submissions →</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function assignmentTitle(lesson: OutlineLesson): string {
  const title = lesson.content.title;
  return typeof title === "string" && title ? title : lesson.title;
}

type StatusFilter = "" | NonNullable<SubmissionFilters["status"]>;

/** /teach/courses/[id]/assignments/[lessonId]: the org's submissions, ungraded first. */
export function SubmissionsPage({ courseId, lessonId }: { courseId: string; lessonId: string }) {
  const course = useQuery(courseQuery(courseId));
  const versionId = course.data?.current_version?.id;
  const version = useQuery({
    ...versionDetailQuery(courseId, versionId ?? ""),
    enabled: !!versionId,
  });
  const lesson = version.data
    ? readOutline(version.data.snapshot)
        .modules.flatMap((m) => m.lessons)
        .find((l) => l.id === lessonId)
    : undefined;
  const [status, setStatus] = useState<StatusFilter>("submitted");
  const [batchId, setBatchId] = useState("");
  const batches = useQuery(allBatchesQuery("active"));
  const query = useInfiniteQuery(
    submissionsQuery(courseId, lessonId, {
      status: status || undefined,
      batchId: batchId || undefined,
    }),
  );
  const rows = query.data?.pages.flatMap((p) => p.items) ?? [];

  if (course.error)
    return <ErrorAlert error={course.error} onRetry={() => void course.refetch()} />;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <Link href={`/teach/courses/${courseId}`} className="text-sm text-muted-foreground">
          ← {course.data?.title ?? "Course"}
        </Link>
        <PageTitle title={lesson ? assignmentTitle(lesson) : "Submissions"} />
        {lesson && typeof lesson.content.max_marks === "number" ? (
          <p className="text-sm text-muted-foreground">Out of {lesson.content.max_marks} marks</p>
        ) : null}
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <NativeSelect
          aria-label="Filter by status"
          className="sm:w-48"
          value={status}
          onChange={(e) => setStatus(e.target.value as StatusFilter)}
        >
          <option value="submitted">To grade</option>
          <option value="graded">Graded</option>
          <option value="">All</option>
        </NativeSelect>
        <NativeSelect
          aria-label="Filter by batch"
          className="sm:w-56"
          value={batchId}
          onChange={(e) => setBatchId(e.target.value)}
        >
          <option value="">All batches</option>
          {(batches.data?.items ?? []).map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </NativeSelect>
        {batches.data?.truncated ? (
          <p className="text-xs text-muted-foreground">Showing the first 2,000 batches.</p>
        ) : null}
      </div>
      {query.isPending ? <PageSkeleton /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && rows.length === 0 ? (
        <EmptyState>{status === "submitted" ? "Nothing to grade." : "No submissions."}</EmptyState>
      ) : null}
      <ul className="flex flex-col gap-2" aria-label="Submissions">
        {rows.map((row) => (
          <SubmissionRow key={row.id} row={row} />
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

function SubmissionRow({ row }: { row: GraderSubmissionRow }) {
  return (
    <li>
      <Link
        href={`/teach/submissions/${row.id}`}
        className="flex items-center justify-between gap-3 rounded-lg border p-3 hover:bg-muted/50"
      >
        <span className="min-w-0">
          <span className="block truncate font-medium">
            {row.student.full_name || row.student.email}
          </span>
          <span className="block truncate text-sm text-muted-foreground">
            Submitted {formatIst(row.submitted_at)}
          </span>
        </span>
        {row.status === "graded" ? (
          <Badge variant="secondary" className="shrink-0 tabular-nums">
            {Number(row.score)} / {row.max_marks}
          </Badge>
        ) : (
          <Badge className="shrink-0">To grade</Badge>
        )}
      </Link>
    </li>
  );
}

/** /teach/submissions/[id]: read the work, then grade it. */
export function SubmissionReviewPage({
  submissionId,
  fromGrading = false,
}: {
  submissionId: string;
  fromGrading?: boolean;
}) {
  const qc = useQueryClient();
  const router = useRouter();
  const query = useQuery(submissionQuery(submissionId));
  const grade = useGrade(submissionId);
  if (query.isPending) return <PageSkeleton />;
  if (query.error) return <ErrorAlert error={query.error} onRetry={() => void query.refetch()} />;
  const detail = query.data;
  const work = detail.submission;
  const back = fromGrading
    ? "/teach/grading"
    : `/teach/courses/${detail.course_id}/assignments/${detail.lesson_id}`;
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <Link href={back} className="text-sm text-muted-foreground">
          ← {detail.assignment.title}
        </Link>
        <PageTitle title={detail.student.full_name || detail.student.email} />
        <p className="text-sm text-muted-foreground">
          {detail.student.email} · submitted {formatIst(work.submitted_at)}
        </p>
      </div>
      <section aria-label="Submission" className="flex flex-col gap-2">
        <h2 className="text-base font-semibold">Submission</h2>
        {work.kind === "text" ? (
          <pre className="max-h-96 overflow-auto rounded-md border bg-muted/40 p-3 text-sm break-words whitespace-pre-wrap">
            {work.text_body}
          </pre>
        ) : work.file?.url ? (
          <p className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium">{work.file.file_name}</span>
            <a
              href={work.file.url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-4"
            >
              Open file
            </a>
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">The file isn&apos;t available.</p>
        )}
      </section>
      <section aria-label="Grading" className="flex max-w-xl flex-col gap-2 rounded-lg border p-4">
        <h2 className="text-base font-semibold">
          {work.grade ? "Grade" : "Grade this submission"}
        </h2>
        <GradeForm
          key={work.revision}
          submission={detail}
          save={(values) => grade.mutateAsync(values)}
          onConflict={() =>
            void qc.invalidateQueries({ queryKey: submissionQuery(submissionId).queryKey })
          }
          onSaved={() => {
            toast.success("Grade saved");
            router.push(back);
          }}
        />
      </section>
    </div>
  );
}
