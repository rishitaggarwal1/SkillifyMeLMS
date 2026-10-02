"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ErrorAlert, errorMessage } from "@/features/admin/ui";
import { ApiError } from "@/lib/api/errors";
import type { StudentAssignment, Submission } from "@/lib/api/types";
import { formatIst } from "@/lib/ist";

import { learnKeys, myAssignmentQuery, useSubmitAssignment } from "./api";
import type { LessonProgress, OutlineLesson } from "./outline";

const MAX_TEXT = 20_000;

type Props = { enrollmentId: string; lesson: OutlineLesson; progress?: LessonProgress };

/** An assignment lesson: instructions, the student's one submission, and the grade. */
export function AssignmentLesson({ enrollmentId, lesson }: Props) {
  const query = useQuery(myAssignmentQuery(enrollmentId, lesson.id));
  if (query.isPending) return <Skeleton className="h-48 w-full" />;
  if (query.error) return <ErrorAlert error={query.error} />;
  const { assignment, submission } = query.data;
  return (
    <div className="flex flex-col gap-5">
      <p className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span>Out of {assignment.max_marks} marks</span>
        {assignment.due_at ? <span>Due {formatIst(assignment.due_at)}</span> : null}
      </p>
      {assignment.instructions_html ? (
        <div
          className="notes-content"
          // Rendered and sanitized by the API at publish time (allow-list + nh3).
          dangerouslySetInnerHTML={{ __html: assignment.instructions_html }}
        />
      ) : null}
      {submission?.status === "graded" && submission.grade ? (
        <Graded submission={submission} />
      ) : (
        <SubmitArea enrollmentId={enrollmentId} lessonId={lesson.id} data={query.data} />
      )}
    </div>
  );
}

function Graded({ submission }: { submission: Submission }) {
  const grade = submission.grade!;
  return (
    <section aria-label="Your grade" className="flex flex-col gap-2 rounded-lg border p-4">
      <p role="status" className="text-sm font-medium text-primary">
        ✓ Completed
      </p>
      <p className="text-2xl font-semibold tabular-nums">
        {Number(grade.score)} / {grade.max_marks}
      </p>
      {grade.feedback ? (
        <div className="flex flex-col gap-1">
          <h3 className="text-sm font-semibold">Feedback</h3>
          <p className="text-sm whitespace-pre-wrap">{grade.feedback}</p>
        </div>
      ) : null}
      <p className="text-xs text-muted-foreground">Graded {formatIst(grade.graded_at)}</p>
      <YourWork submission={submission} />
    </section>
  );
}

function YourWork({ submission }: { submission: Submission }) {
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-muted-foreground">
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
          className="mt-2 inline-block underline underline-offset-4"
        >
          {submission.file.file_name}
        </a>
      ) : (
        <p className="mt-2">{submission.file?.file_name}</p>
      )}
    </details>
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
  const ready = kind === "text" ? text.trim().length > 0 && text.length <= MAX_TEXT : !!file;

  async function send() {
    setError(null);
    try {
      if (kind === "text") {
        await submit.mutateAsync({ kind, text, revision });
      } else if (file) {
        setProgress(0);
        await submit.mutateAsync({ kind, file, revision, onProgress: setProgress });
        setFile(null);
      }
      toast.success(submission ? "Submission replaced" : "Submitted");
      void qc.invalidateQueries({ queryKey: learnKeys.enrollment(enrollmentId) });
    } catch (e) {
      if (e instanceof ApiError && e.code === "revision_conflict") {
        setError("Your submission changed on another device. The latest is shown.");
      } else if (e instanceof ApiError && e.code === "already_graded") {
        setError("This assignment has just been graded.");
      } else {
        setError(errorMessage(e));
      }
    } finally {
      setProgress(null);
    }
  }

  return (
    <section aria-label="Your submission" className="flex flex-col gap-3">
      {submission ? (
        <div className="flex flex-col gap-1 rounded-lg border p-3">
          <p className="flex flex-wrap items-center gap-2 text-sm">
            <Badge variant="secondary">Submitted</Badge>
            <span className="text-muted-foreground">
              {formatIst(submission.submitted_at)}. You can replace it until it&apos;s graded.
            </span>
          </p>
          <YourWork submission={submission} />
        </div>
      ) : null}
      {kinds.length > 1 ? (
        <fieldset className="flex flex-wrap gap-4 text-sm">
          <legend className="sr-only">How to submit</legend>
          {kinds.map((k) => (
            <label key={k} className="flex items-center gap-2">
              <input
                type="radio"
                name={`kind-${lessonId}`}
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
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`answer-${lessonId}`}>Your answer</Label>
          <Textarea
            id={`answer-${lessonId}`}
            rows={8}
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="font-mono text-sm"
          />
          <p className="text-xs text-muted-foreground">
            {text.length.toLocaleString("en-IN")} / {MAX_TEXT.toLocaleString("en-IN")} characters
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`file-${lessonId}`}>File (PDF, PNG or JPEG)</Label>
          <Input
            id={`file-${lessonId}`}
            type="file"
            accept="application/pdf,image/png,image/jpeg"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
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
    </section>
  );
}
