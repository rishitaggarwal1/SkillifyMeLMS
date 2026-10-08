"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { GradeBreakdown, LateStatus } from "@/components/patterns/assessment";
import { ErrorAlert, LoadMore } from "@/components/patterns/page";
import { PublishedContent } from "@/components/patterns/published-content";
import { PageSkeleton } from "@/components/patterns/states";
import { Button } from "@/components/ui/button";
import type { Rubric, Submission, SubmissionAttempt } from "@/lib/api/types";
import { formatIst } from "@/lib/ist";

import { assignmentAttemptsQuery, assignmentGradesQuery } from "./api";

export function YourWork({
  submission,
}: {
  submission: Pick<Submission, "kind" | "text_body" | "file" | "submitted_at">;
}) {
  return (
    <details className="text-sm">
      <summary className="min-h-11 cursor-pointer py-3 text-muted-foreground">
        Your submission ({formatIst(submission.submitted_at)})
      </summary>
      {submission.kind === "text" ? (
        <pre className="mt-2 max-h-72 overflow-auto rounded-md border bg-muted/40 p-3 break-words whitespace-pre-wrap">
          {submission.text_body}
        </pre>
      ) : submission.file?.url ? (
        <a
          href={submission.file.url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-flex min-h-11 items-center underline underline-offset-4"
        >
          {submission.file.file_name}
        </a>
      ) : (
        <p className="mt-2">{submission.file?.file_name}</p>
      )}
    </details>
  );
}
export function PinnedRubric({ rubric }: { rubric?: Rubric | null }) {
  if (!rubric) return null;
  return (
    <section aria-label="Rubric" className="flex flex-col gap-2">
      <h4>Rubric</h4>
      <ul className="flex flex-col gap-2">
        {rubric.criteria.map((criterion) => (
          <li key={criterion.id}>
            <p>
              {criterion.label} · {Number(criterion.max_marks)} marks
            </p>
            {criterion.description ? (
              <p className="text-sm text-muted-foreground">{criterion.description}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
export function AssignmentHistory({
  enrollmentId,
  lessonId,
}: {
  enrollmentId: string;
  lessonId: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="min-h-11 cursor-pointer py-3 font-semibold">
        Submission and grade history
      </summary>
      {open ? <AttemptHistory enrollmentId={enrollmentId} lessonId={lessonId} /> : null}
    </details>
  );
}
function AttemptHistory({ enrollmentId, lessonId }: { enrollmentId: string; lessonId: string }) {
  const history = useInfiniteQuery(assignmentAttemptsQuery(enrollmentId, lessonId));
  const [selected, setSelected] = useState<string | null>(null);
  if (history.isPending) return <PageSkeleton rows={1} />;
  if (history.error)
    return <ErrorAlert error={history.error} onRetry={() => void history.refetch()} />;
  const attempts = history.data.pages.flatMap((p) => p.items);
  const attempt = attempts.find((a) => a.id === selected) ?? attempts[0];
  return (
    <section aria-label="Submission attempt history" className="flex flex-col gap-4">
      <ul className="flex flex-col gap-2">
        {attempts.map((a) => (
          <li key={a.id}>
            <Button
              variant={attempt?.id === a.id ? "secondary" : "outline"}
              onClick={() => setSelected(a.id)}
            >
              {"Attempt " +
                a.attempt_number +
                (a.is_active ? " · Active" : "") +
                " · " +
                formatIst(a.submitted_at)}
            </Button>
          </li>
        ))}
      </ul>
      <LoadMore
        hasNextPage={history.hasNextPage}
        isFetchingNextPage={history.isFetchingNextPage}
        onClick={() => void history.fetchNextPage()}
      />
      {attempt ? (
        <HistoricalAttempt
          key={attempt.id}
          enrollmentId={enrollmentId}
          lessonId={lessonId}
          attempt={attempt}
        />
      ) : null}
    </section>
  );
}
function HistoricalAttempt({
  enrollmentId,
  lessonId,
  attempt,
}: {
  enrollmentId: string;
  lessonId: string;
  attempt: SubmissionAttempt;
}) {
  const grades = useInfiniteQuery(assignmentGradesQuery(enrollmentId, lessonId, attempt.id));
  return (
    <div className="state-card flex flex-col gap-4">
      <section aria-label="Pinned instructions">
        <h4>Instructions for this attempt</h4>
        <PublishedContent
          html={attempt.assignment.instructions_html}
          imageUrls={attempt.image_urls}
        />
      </section>
      <PinnedRubric rubric={attempt.assignment.rubric} />
      <LateStatus late={attempt.late} />
      <YourWork submission={attempt} />
      <section aria-label="Grade history" className="flex flex-col gap-3">
        <h4>Grade history</h4>
        {grades.isPending ? (
          <PageSkeleton rows={1} />
        ) : grades.error ? (
          <ErrorAlert error={grades.error} onRetry={() => void grades.refetch()} />
        ) : grades.data.pages.some((p) => p.items.length) ? (
          <ul className="flex flex-col gap-4">
            {grades.data.pages
              .flatMap((p) => p.items)
              .map((grade) => (
                <li key={grade.id ?? grade.graded_at}>
                  <GradeBreakdown grade={grade} rubric={attempt.assignment.rubric} />
                </li>
              ))}
          </ul>
        ) : (
          <p>This attempt has not been graded.</p>
        )}
        <LoadMore
          hasNextPage={grades.hasNextPage}
          isFetchingNextPage={grades.isFetchingNextPage}
          onClick={() => void grades.fetchNextPage()}
        />
      </section>
    </div>
  );
}
