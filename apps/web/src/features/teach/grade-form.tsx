"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField, LiveAnnouncement } from "@/components/patterns/states";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/features/admin/ui";
import { ApiError } from "@/lib/api/errors";
import type { GraderSubmission, Rubric } from "@/lib/api/types";
import { gradePreview } from "@/lib/marks";

import type { GradeValues } from "./assignment-api";

export const MAX_FEEDBACK = 10_000;

/** Score: 0..max marks, at most two decimals (the API stores two). */
export function gradeSchema(maxMarks: number) {
  return z.object({
    score: z
      .string()
      .trim()
      .regex(/^\d{1,4}(\.\d{1,2})?$/, "A number with at most two decimals.")
      .refine((v) => Number(v) <= maxMarks, `At most ${maxMarks}.`),
    feedback: z.string().max(MAX_FEEDBACK, `At most ${MAX_FEEDBACK} characters.`),
  });
}
export function rubricGradeSchema(maxMarks: number, rubric?: Rubric | null) {
  return z
    .object({
      score: z.string(),
      criteria: z.array(z.string()),
      feedback: z.string().max(MAX_FEEDBACK, "At most " + MAX_FEEDBACK + " characters."),
    })
    .superRefine((values, ctx) => {
      if (rubric) {
        if (values.criteria.length !== rubric.criteria.length)
          ctx.addIssue({
            code: "custom",
            path: ["criteria"],
            message: "Score every rubric criterion.",
          });
        rubric.criteria.forEach((criterion, i) => {
          const result = gradeSchema(Number(criterion.max_marks)).shape.score.safeParse(
            values.criteria[i],
          );
          if (!result.success)
            ctx.addIssue({
              code: "custom",
              path: ["criteria", i],
              message: result.error.issues[0]?.message ?? "Enter a valid criterion score.",
            });
        });
      } else {
        const result = gradeSchema(maxMarks).shape.score.safeParse(values.score);
        if (!result.success)
          ctx.addIssue({
            code: "custom",
            path: ["score"],
            message: result.error.issues[0]?.message ?? "Enter a valid score.",
          });
      }
    });
}
type GradeForm = z.infer<ReturnType<typeof rubricGradeSchema>>;

type Props = {
  submission: GraderSubmission;
  /** Saves the grade (sent with the submission revision as If-Match). */
  save: (values: GradeValues) => Promise<unknown>;
  /** Someone changed the submission meanwhile (409): reload it. */
  onConflict: () => void;
  onSaved?: () => void;
};

export function GradeForm({ submission, save, onConflict, onSaved }: Props) {
  const maxMarks = submission.assignment.max_marks;
  const current = submission.submission.grade;
  const rubric = submission.assignment.rubric;
  const form = useForm<GradeForm>({
    resolver: zodResolver(rubricGradeSchema(maxMarks, rubric)),
    defaultValues: {
      score: current ? String(Number(current.raw_score ?? current.score)) : "",
      criteria:
        rubric?.criteria.map((c) =>
          String(
            current?.rubric_breakdown?.find((item) => item.criterion_id === c.id)?.score ?? "",
          ),
        ) ?? [],
      feedback: current?.feedback ?? "",
    },
  });
  const errors = form.formState.errors;
  const values = useWatch({ control: form.control });
  const preview = gradePreview(
    rubric ? (values.criteria ?? []) : [values.score ?? ""],
    submission.submission.late?.penalty_percent ?? "0",
  );

  async function submit(values: GradeForm) {
    try {
      await save({
        ...(rubric
          ? {
              criterion_scores: rubric.criteria.map((c, i) => ({
                criterion_id: c.id,
                score: (values.criteria[i] ?? "").trim(),
              })),
            }
          : { score: values.score }),
        ...(submission.submission.active_attempt_id
          ? { attempt_id: submission.submission.active_attempt_id }
          : {}),
        feedback: values.feedback,
        revision: submission.submission.revision,
      });
      onSaved?.();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        form.setError("root", {
          message: "This submission changed since you opened it. The latest is shown; grade again.",
        });
        onConflict();
        return;
      }
      form.setError("root", { message: errorMessage(error) });
    }
  }

  return (
    <form
      onSubmit={form.handleSubmit(submit)}
      className="flex flex-col gap-3"
      aria-label="Grade"
      noValidate
    >
      {rubric ? (
        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 font-medium">Rubric scoring</legend>
          {rubric.criteria.map((c, i) => (
            <FormField
              key={c.id}
              label={c.label + " (out of " + Number(c.max_marks) + ")"}
              help={c.description ?? undefined}
              error={errors.criteria?.[i]?.message}
              saving={form.formState.isSubmitting}
            >
              {(props) => (
                <Input
                  {...props}
                  inputMode="decimal"
                  autoComplete="off"
                  {...form.register(("criteria." + i) as "criteria.0")}
                />
              )}
            </FormField>
          ))}
        </fieldset>
      ) : (
        <div className="w-40">
          <FormField
            label={`Score (out of ${maxMarks})`}
            error={errors.score?.message}
            saving={form.formState.isSubmitting}
          >
            {(props) => (
              <Input
                {...props}
                inputMode="decimal"
                autoComplete="off"
                {...form.register("score")}
              />
            )}
          </FormField>
        </div>
      )}
      {preview ? (
        <dl
          aria-label="Grade preview"
          aria-live="polite"
          className="grid grid-cols-2 gap-2 rounded-md bg-muted/40 p-3 text-sm tabular-nums"
        >
          <dt>Earned marks</dt>
          <dd>{preview.raw_score}</dd>
          <dt>Late penalty ({Number(preview.penalty_percent)}%)</dt>
          <dd>{preview.penalty_marks}</dd>
          <dt className="font-semibold">Final score</dt>
          <dd className="font-semibold">
            {preview.score} / {maxMarks}
          </dd>
        </dl>
      ) : (
        <p className="text-sm text-muted-foreground">
          Enter every score to preview earned marks and the frozen late penalty.
        </p>
      )}
      <FormField
        label="Feedback for the student (optional)"
        error={errors.feedback?.message}
        saving={form.formState.isSubmitting}
      >
        {(props) => <Textarea {...props} rows={4} {...form.register("feedback")} />}
      </FormField>
      <LiveAnnouncement>{form.formState.isSubmitting ? "Saving grade" : ""}</LiveAnnouncement>
      {errors.root ? (
        <p role="alert" className="text-sm text-destructive">
          {errors.root.message}
        </p>
      ) : null}
      <div>
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? "Saving…" : current ? "Update grade" : "Save grade"}
        </Button>
      </div>
    </form>
  );
}
