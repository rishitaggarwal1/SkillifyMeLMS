"use client";

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "@/lib/toast";

import { GradeBreakdown, LateStatus } from "@/components/patterns/assessment";
import { ErrorAlert, errorMessage } from "@/components/patterns/page";
import { PublishedContent } from "@/components/patterns/published-content";
import { FormField, LiveAnnouncement, PageSkeleton } from "@/components/patterns/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api/errors";
import type { Rubric, StudentAssignment, Submission } from "@/lib/api/types";
import { formatIst } from "@/lib/ist";

import {
  assignmentAttemptsQuery,
  learnKeys,
  myAssignmentQuery,
  notesImagesQuery,
  useSubmitAssignment,
} from "./api";
import { AssignmentHistory, PinnedRubric, YourWork } from "./assignment-history";
import type { LessonProgress, OutlineLesson } from "./outline";

const MAX_TEXT = 20_000;
type Props = { enrollmentId: string; lesson: OutlineLesson; progress?: LessonProgress };

/** Extend the existing text/file flow with frozen late data and attempt history. */
export function AssignmentLesson({ enrollmentId, lesson }: Props) {
  const qc = useQueryClient();
  const query = useQuery(myAssignmentQuery(enrollmentId, lesson.id));
  const history = useInfiniteQuery({
    ...assignmentAttemptsQuery(enrollmentId, lesson.id),
    enabled: !!query.data?.submission,
  });
  const active = history.data?.pages.flatMap((p) => p.items).find((a) => a.is_active);
  const definition =
    query.data?.submission?.status === "graded" && active
      ? active.assignment
      : query.data?.assignment;
  const images = useQuery({
    ...notesImagesQuery(enrollmentId, lesson.id),
    enabled: !!definition?.image_file_ids?.length && query.data?.submission?.status !== "graded",
  });
  const gradeStamp = query.data?.submission?.grade?.graded_at;
  const gradeSequence = query.data?.submission?.grade?.grade_sequence;
  useEffect(() => {
    if (!gradeStamp) return;
    void qc.invalidateQueries({ queryKey: learnKeys.enrollment(enrollmentId) });
    void qc.invalidateQueries({ queryKey: learnKeys.enrollments, exact: true });
    void qc.invalidateQueries({ queryKey: ["dashboards", "results"] });
    void qc.invalidateQueries({
      queryKey: assignmentAttemptsQuery(enrollmentId, lesson.id).queryKey,
    });
  }, [enrollmentId, lesson.id, qc, gradeStamp, gradeSequence]);
  if (query.isPending) return <PageSkeleton />;
  if (!query.data || (query.error instanceof ApiError && query.error.status === 404))
    return <ErrorAlert error={query.error} onRetry={() => void query.refetch()} />;
  const { assignment, submission } = query.data;
  const displayed = definition ?? assignment;
  return (
    <div className="flex flex-col gap-5">
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      <p className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span>Out of {displayed.max_marks} marks</span>
        {displayed.due_at ? <span>Due {formatIst(displayed.due_at)}</span> : null}
      </p>
      <PublishedContent
        html={displayed.instructions_html}
        imageUrls={submission?.status === "graded" ? active?.image_urls : images.data?.urls}
      />
      {images.error ? (
        <ErrorAlert error={images.error} onRetry={() => void images.refetch()} />
      ) : null}
      {history.error ? (
        <ErrorAlert error={history.error} onRetry={() => void history.refetch()} />
      ) : null}
      {submission?.status === "graded" && submission.grade ? (
        <Graded submission={submission} rubric={active?.assignment.rubric} />
      ) : (
        <SubmitArea enrollmentId={enrollmentId} lessonId={lesson.id} data={query.data} />
      )}
      {submission ? <AssignmentHistory enrollmentId={enrollmentId} lessonId={lesson.id} /> : null}
    </div>
  );
}
function Graded({ submission, rubric }: { submission: Submission; rubric?: Rubric | null }) {
  return (
    <section aria-label="Your grade" className="flex flex-col gap-3 rounded-lg border p-4">
      <p role="status" className="text-sm font-medium text-primary">
        ✓ Completed
      </p>
      <LateStatus late={submission.late} />
      <GradeBreakdown grade={submission.grade!} rubric={rubric} />
      <YourWork submission={submission} />
    </section>
  );
}
function SubmitArea({
  enrollmentId,
  lessonId,
  data,
}: {
  enrollmentId: string;
  lessonId: string;
  data: StudentAssignment;
}) {
  const qc = useQueryClient();
  const { assignment, submission } = data;
  const kinds = assignment.submission_kinds;
  const [kind, setKind] = useState<"text" | "file">(
    submission?.kind ?? (kinds.includes("text") ? "text" : "file"),
  );
  const [text, setText] = useState(submission?.text_body ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const submit = useSubmitAssignment(enrollmentId, lessonId);
  const revision = submission?.revision ?? 0;
  const ready =
    !data.late?.closed &&
    (kind === "text" ? text.trim().length > 0 && text.length <= MAX_TEXT : !!file);

  async function send() {
    setError(null);
    try {
      if (kind === "text") await submit.mutateAsync({ kind, text, revision });
      else if (file) {
        setProgress(0);
        await submit.mutateAsync({ kind, file, revision, onProgress: setProgress });
        setFile(null);
      }
      toast.success(submission ? "Submission replaced" : "Submitted");
      void qc.invalidateQueries({ queryKey: learnKeys.enrollment(enrollmentId) });
      void qc.invalidateQueries({ queryKey: ["dashboards", "results"] });
      void qc.invalidateQueries({ queryKey: ["dashboards", "due"] });
    } catch (e) {
      if (e instanceof ApiError && e.code === "revision_conflict")
        setError(
          "Your submission changed on another device. Your unsaved work is kept; review it before replacing the latest submission.",
        );
      else if (e instanceof ApiError && e.code === "already_graded")
        setError("This assignment has just been graded.");
      else setError(errorMessage(e));
    } finally {
      setProgress(null);
    }
  }
  return (
    <section aria-label="Your submission" className="flex flex-col gap-3">
      <section aria-label="Before submitting" className="state-card flex flex-col gap-3">
        <h3>Before submitting</h3>
        <LateStatus late={data.late} />
        {data.late?.closed ? (
          <p>This assignment is closed to new submissions. Your saved work is kept.</p>
        ) : null}
        {data.server_time ? (
          <p className="text-xs text-muted-foreground">
            Checked by the server at {formatIst(data.server_time)}. The status is checked again when
            you submit.
          </p>
        ) : null}
        <PinnedRubric rubric={assignment.rubric} />
      </section>
      {submission ? (
        <div className="flex flex-col gap-2 rounded-lg border p-3">
          <p className="flex flex-wrap items-center gap-2 text-sm">
            <Badge variant="secondary">Submitted</Badge>
            <span className="text-muted-foreground">
              {formatIst(submission.submitted_at)}. You can replace it until it&apos;s graded.
            </span>
          </p>
          <YourWork submission={submission} />
          <p className="text-sm font-medium">Accepted submission status</p>
          <LateStatus late={submission.late} />
        </div>
      ) : null}
      {kinds.length > 1 ? (
        <fieldset
          disabled={submit.isPending || !!data.late?.closed}
          className="flex flex-wrap gap-4 text-sm"
        >
          <legend className="sr-only">How to submit</legend>
          {kinds.map((k) => (
            <label key={k} className="flex min-h-11 items-center gap-2">
              <input
                type="radio"
                name={"kind-" + lessonId}
                value={k}
                checked={kind === k}
                onChange={() => setKind(k)}
              />
              {k === "text" ? "Type your answer" : "Upload a file"}
            </label>
          ))}
        </fieldset>
      ) : null}
      {kind === "text" ? (
        <FormField
          id={"answer-" + lessonId}
          label="Your answer"
          help={
            text.length.toLocaleString("en-IN") +
            " / " +
            MAX_TEXT.toLocaleString("en-IN") +
            " characters"
          }
          saving={submit.isPending}
          disabled={!!data.late?.closed}
        >
          {(props) => (
            <Textarea
              {...props}
              rows={8}
              maxLength={MAX_TEXT}
              value={text}
              onChange={(e) => setText(e.target.value)}
              className="font-mono text-sm"
            />
          )}
        </FormField>
      ) : (
        <FormField
          id={"file-" + lessonId}
          label="File (PDF, PNG or JPEG)"
          saving={submit.isPending}
          disabled={!!data.late?.closed}
        >
          {(props) => (
            <Input
              {...props}
              type="file"
              accept="application/pdf,image/png,image/jpeg"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          )}
        </FormField>
      )}
      {progress !== null ? (
        <progress aria-label="Upload progress" max={1} value={progress} className="w-full" />
      ) : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <div>
        <Button onClick={() => void send()} disabled={!ready || submit.isPending}>
          {submit.isPending ? "Submitting…" : submission ? "Replace submission" : "Submit"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        The lesson completes when your instructor grades it.
      </p>
      <LiveAnnouncement>
        {submit.isPending ? "Submitting your work" : submission ? "Work submitted" : ""}
      </LiveAnnouncement>
    </section>
  );
}
