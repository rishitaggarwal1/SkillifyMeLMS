"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/features/admin/ui";
import { ApiError } from "@/lib/api/errors";
import type { GraderSubmission } from "@/lib/api/types";

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
type GradeForm = z.infer<ReturnType<typeof gradeSchema>>;

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
  const form = useForm<GradeForm>({
    resolver: zodResolver(gradeSchema(maxMarks)),
    defaultValues: {
      score: current ? String(Number(current.score)) : "",
      feedback: current?.feedback ?? "",
    },
  });
  const errors = form.formState.errors;

  async function submit(values: GradeForm) {
    try {
      await save({ ...values, revision: submission.submission.revision });
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
      <div className="flex w-40 flex-col gap-1.5">
        <Label htmlFor="grade-score">Score (out of {maxMarks})</Label>
        <Input
          id="grade-score"
          inputMode="decimal"
          autoComplete="off"
          aria-invalid={!!errors.score}
          {...form.register("score")}
        />
        {errors.score ? <p className="text-sm text-destructive">{errors.score.message}</p> : null}
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="grade-feedback">Feedback for the student (optional)</Label>
        <Textarea id="grade-feedback" rows={4} {...form.register("feedback")} />
        {errors.feedback ? (
          <p className="text-sm text-destructive">{errors.feedback.message}</p>
        ) : null}
      </div>
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
