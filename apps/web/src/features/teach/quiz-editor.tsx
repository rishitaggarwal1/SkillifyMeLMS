"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useFieldArray, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { NativeSelect } from "@/components/native-select";
import { EmptyState, ErrorAlert, LoadMore, errorMessage } from "@/components/patterns/page";
import { FormField, PageSkeleton } from "@/components/patterns/states";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useDebounced } from "@/features/admin/hooks";
import type { DraftQuiz, QuizBody, Skill } from "@/lib/api/types";
import { cents, marks } from "@/lib/marks";

import { allSkillsQuery } from "./api";
import {
  bankQuery,
  bankQuestionsQuery,
  banksQuery,
  draftQuizQuery,
  useSaveQuiz,
} from "./assessment-api";
import { QUESTION_TYPES } from "./question-form";
import { SkillsPicker } from "./skills-picker";

const positive = (v: string) => {
  const n = cents(v);
  return n !== null && n > 0n && n <= 9999999999n;
};
export const quizFormSchema = z
  .object({
    title: z.string().trim().min(1, "Title is required.").max(200),
    mode: z.enum(["manual", "bank"]),
    bank_id: z.string(),
    questions: z.array(z.object({ question_id: z.string(), marks: z.string() })).max(100),
    draw_count: z.string(),
    marks_per_question: z.string(),
    skill_ids: z.array(z.string()).max(50),
    pass_marks: z.string().trim().refine(positive, "Use positive marks with at most two decimals."),
    time_limit_seconds: z
      .string()
      .trim()
      .refine((v) => /^\d+$/.test(v) && +v >= 1 && +v <= 86400, "1 to 86,400 seconds."),
    attempts_allowed: z
      .string()
      .trim()
      .refine((v) => /^\d+$/.test(v) && +v >= 1 && +v <= 100, "1 to 100 attempts."),
    randomize_order: z.boolean(),
    reveal_mode: z.enum(["score_only", "correct_answers", "explanations"]),
    reveal_timing: z.enum(["immediately", "after_attempts_exhausted"]),
  })
  .superRefine((value, ctx) => {
    const issue = (path: (string | number)[], message: string) =>
      ctx.addIssue({ code: "custom", path, message });
    if (value.mode === "manual") {
      if (!value.questions.length) issue(["questions"], "Select at least one question.");
      if (new Set(value.questions.map((q) => q.question_id)).size !== value.questions.length)
        issue(["questions"], "Select each question only once.");
      value.questions.forEach((q, i) => {
        if (!positive(q.marks))
          issue(["questions", i, "marks"], "Use positive marks with at most two decimals.");
      });
    } else {
      if (!value.bank_id) issue(["bank_id"], "Choose a question bank.");
      if (!/^\d+$/.test(value.draw_count) || +value.draw_count < 1 || +value.draw_count > 100)
        issue(["draw_count"], "Draw 1 to 100 questions.");
      if (!positive(value.marks_per_question))
        issue(["marks_per_question"], "Use positive marks with at most two decimals.");
    }
    const maximum = quizMaximum(value);
    if (maximum !== null && maximum > 9999999999n)
      issue(["pass_marks"], "Maximum marks are too large.");
    if (maximum !== null && (cents(value.pass_marks) ?? 0n) > maximum)
      issue(["pass_marks"], "Pass marks cannot exceed maximum marks.");
  });
export type QuizFormValues = z.infer<typeof quizFormSchema>;
export function quizMaximum(
  values: Pick<QuizFormValues, "mode" | "questions" | "draw_count" | "marks_per_question">,
): bigint | null {
  if (values.mode === "bank") {
    const unit = cents(values.marks_per_question);
    return unit !== null && /^\d+$/.test(values.draw_count)
      ? unit * BigInt(values.draw_count)
      : null;
  }
  const valuesInCents = values.questions.map((q) => cents(q.marks));
  return valuesInCents.some((n) => n === null)
    ? null
    : valuesInCents.reduce<bigint>((a, b) => a + b!, 0n);
}
export function quizFields(values: QuizFormValues): QuizBody {
  return {
    title: values.title,
    selection:
      values.mode === "manual"
        ? { mode: "manual", questions: values.questions }
        : {
            mode: "bank",
            bank_id: values.bank_id,
            draw_count: Number(values.draw_count),
            marks_per_question: values.marks_per_question,
            skill_ids: values.skill_ids,
          },
    pass_marks: values.pass_marks,
    time_limit_seconds: Number(values.time_limit_seconds),
    attempts_allowed: Number(values.attempts_allowed),
    randomize_order: values.randomize_order,
    reveal_mode: values.reveal_mode,
    reveal_timing: values.reveal_timing,
  };
}
function initial(draft: DraftQuiz | null, lessonTitle: string): QuizFormValues {
  const selection = draft?.selection;
  return {
    title: draft?.title ?? lessonTitle,
    mode: selection?.mode ?? "manual",
    bank_id:
      selection?.mode === "bank"
        ? selection.bank_id
        : (draft?.question_summaries?.[0]?.bank_id ?? ""),
    questions:
      selection?.mode === "manual"
        ? selection.questions.map((q) => ({ ...q, marks: String(q.marks) }))
        : [],
    draw_count: selection?.mode === "bank" ? String(selection.draw_count) : "1",
    marks_per_question: selection?.mode === "bank" ? String(selection.marks_per_question) : "1",
    skill_ids: selection?.mode === "bank" ? (selection.skill_ids ?? []) : [],
    pass_marks: String(draft?.pass_marks ?? 1),
    time_limit_seconds: String(draft?.time_limit_seconds ?? 600),
    attempts_allowed: String(draft?.attempts_allowed ?? 1),
    randomize_order: draft?.randomize_order ?? false,
    reveal_mode: draft?.reveal_mode ?? "score_only",
    reveal_timing: draft?.reveal_timing ?? "immediately",
  };
}
export function QuizEditor({
  courseId,
  lessonId,
  lessonTitle,
}: {
  courseId: string;
  lessonId: string;
  lessonTitle: string;
}) {
  const query = useQuery(draftQuizQuery(courseId, lessonId));
  if (query.isPending) return <PageSkeleton kind="form" />;
  if (query.error) return <ErrorAlert error={query.error} onRetry={() => void query.refetch()} />;
  return (
    <QuizEditorForm
      key={lessonId}
      courseId={courseId}
      lessonId={lessonId}
      lessonTitle={lessonTitle}
      draft={query.data}
    />
  );
}

function QuizEditorForm({
  courseId,
  lessonId,
  lessonTitle,
  draft,
}: {
  courseId: string;
  lessonId: string;
  lessonTitle: string;
  draft: DraftQuiz | null;
}) {
  const save = useSaveQuiz(courseId, lessonId);
  const form = useForm<QuizFormValues>({
    resolver: zodResolver(quizFormSchema),
    defaultValues: initial(draft, lessonTitle),
  });
  const chosen = useFieldArray({ control: form.control, name: "questions", keyName: "rowKey" });
  const values = useWatch({ control: form.control });
  const [bankSearch, setBankSearch] = useState("");
  const banks = useInfiniteQuery(banksQuery(useDebounced(bankSearch.trim(), 250)));
  const selectedBank = useQuery({ ...bankQuery(values.bank_id ?? ""), enabled: !!values.bank_id });
  const available = useInfiniteQuery({
    ...bankQuestionsQuery(values.bank_id ?? "", {
      skills: values.mode === "bank" ? values.skill_ids : [],
    }),
    enabled: !!values.bank_id,
  });
  const taxonomy = useQuery(allSkillsQuery());
  const [skillNames, setSkillNames] = useState(new Map<string, Skill>());
  const [questionNames, setQuestionNames] = useState(
    () => new Map((draft?.question_summaries ?? []).map((q) => [q.id, q])),
  );
  const errors = form.formState.errors;
  const busy = form.formState.isSubmitting;
  // Field-array membership changes synchronously with append/remove; the
  // broad value watch can lag that render during rapid consecutive selections.
  const selectedIds = new Set(chosen.fields.map((q) => q.question_id));
  const maximum = quizMaximum(form.getValues());
  const bankItems = banks.data?.pages.flatMap((p) => p.items) ?? [];
  const questionItems = available.data?.pages.flatMap((p) => p.items) ?? [];
  async function submit(input: QuizFormValues) {
    try {
      const saved = await save.mutateAsync(quizFields(input));
      setQuestionNames(new Map((saved.question_summaries ?? []).map((q) => [q.id, q])));
      form.reset(initial(saved, lessonTitle));
      toast.success("Quiz saved");
    } catch (error) {
      form.setError("root", {
        message:
          errorMessage(error) +
          " Your edits are kept; review the latest course before saving again.",
      });
    }
  }
  return (
    <section aria-label="Quiz builder" className="flex flex-col gap-4">
      <h2>Quiz</h2>
      <p className="text-sm text-muted-foreground">
        Passing completes this lesson. Keys, types/options, question sets, marks, pass threshold,
        time limit and attempts require a major version when changed.
      </p>
      <form
        aria-label="Quiz details"
        noValidate
        onSubmit={form.handleSubmit(submit)}
        className="flex max-w-3xl flex-col gap-4"
      >
        <FormField label="Quiz title" error={errors.title?.message} saving={busy}>
          {(props) => <Input {...props} {...form.register("title")} />}
        </FormField>
        <FormField label="Choose questions" saving={busy}>
          {(props) => (
            <NativeSelect {...props} {...form.register("mode")}>
              <option value="manual">Select questions and marks</option>
              <option value="bank">Draw from a bank</option>
            </NativeSelect>
          )}
        </FormField>
        <FormField label="Search question banks" saving={busy}>
          {(props) => (
            <Input
              {...props}
              type="search"
              maxLength={200}
              value={bankSearch}
              onChange={(e) => setBankSearch(e.target.value)}
            />
          )}
        </FormField>
        <FormField label="Question bank" error={errors.bank_id?.message} saving={busy}>
          {(props) => (
            <NativeSelect {...props} {...form.register("bank_id")}>
              <option value="">Choose a bank</option>
              {values.bank_id && !bankItems.some((b) => b.id === values.bank_id) ? (
                <option value={values.bank_id}>{selectedBank.data?.name ?? "Selected bank"}</option>
              ) : null}
              {bankItems.map((bank) => (
                <option key={bank.id} value={bank.id}>
                  {bank.name}
                </option>
              ))}
            </NativeSelect>
          )}
        </FormField>
        {banks.error ? (
          <ErrorAlert error={banks.error} onRetry={() => void banks.refetch()} />
        ) : null}
        <LoadMore
          hasNextPage={banks.hasNextPage}
          isFetchingNextPage={banks.isFetchingNextPage}
          onClick={() => void banks.fetchNextPage()}
        />
        {banks.isSuccess && !bankItems.length && !bankSearch ? (
          <EmptyState href="/teach/question-banks" actionLabel="Create a question bank">
            Add reusable questions before building this quiz.
          </EmptyState>
        ) : null}
        {values.mode === "bank" ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                label="Number of questions to draw"
                error={errors.draw_count?.message}
                saving={busy}
              >
                {(props) => (
                  <Input {...props} inputMode="numeric" {...form.register("draw_count")} />
                )}
              </FormField>
              <FormField
                label="Marks per question"
                error={errors.marks_per_question?.message}
                saving={busy}
              >
                {(props) => (
                  <Input {...props} inputMode="decimal" {...form.register("marks_per_question")} />
                )}
              </FormField>
            </div>
            <SkillsPicker
              selected={values.skill_ids ?? []}
              names={new Map([...(taxonomy.data ?? []), ...skillNames])}
              disabled={busy}
              onChange={(ids, skill) => {
                form.setValue("skill_ids", ids, { shouldDirty: true });
                if (skill) setSkillNames((v) => new Map(v).set(skill.id, skill));
              }}
            />
            <p className="text-sm text-muted-foreground">
              Each attempt draws this many active questions matching all selected skills.
            </p>
            <section aria-label="Eligible questions" className="flex flex-col gap-2">
              <h3 className="font-medium">Eligible questions</h3>
              <ul className="flex flex-col gap-2">
                {questionItems.map((q) => (
                  <li key={q.id} className="rounded-md border p-3 text-sm break-words">
                    {q.prompt}{" "}
                    <span className="block text-muted-foreground">
                      {QUESTION_TYPES[q.question_type]}
                    </span>
                  </li>
                ))}
              </ul>
              {available.isSuccess && questionItems.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No active questions match these skills. Broaden the filter or add matching
                  questions.
                </p>
              ) : null}
            </section>
          </>
        ) : (
          <>
            <fieldset className="flex min-w-0 flex-col gap-2">
              <legend className="mb-2 font-medium">Available questions</legend>
              {!values.bank_id ? (
                <p className="text-sm text-muted-foreground">Choose a bank to see its questions.</p>
              ) : null}
              {questionItems.map((question) => (
                <label
                  key={question.id}
                  className="flex min-w-0 items-start gap-3 rounded-lg border p-3 text-sm"
                >
                  <Checkbox
                    disabled={
                      busy || (!selectedIds.has(question.id) && chosen.fields.length >= 100)
                    }
                    checked={selectedIds.has(question.id)}
                    onCheckedChange={(on) => {
                      const current = form.getValues("questions");
                      if (on === true) {
                        if (current.some((q) => q.question_id === question.id)) return;
                        chosen.append({ question_id: question.id, marks: "1" });
                        setQuestionNames((v) =>
                          new Map(v).set(question.id, {
                            id: question.id,
                            bank_id: question.bank_id,
                            prompt: question.prompt,
                            question_type: question.question_type,
                            archived: false,
                          }),
                        );
                      } else chosen.remove(current.findIndex((q) => q.question_id === question.id));
                    }}
                  />
                  <span className="min-w-0 break-words">
                    {question.prompt}
                    <span className="block text-muted-foreground">
                      {QUESTION_TYPES[question.question_type]}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>
            <fieldset className="flex min-w-0 flex-col gap-3">
              <legend className="mb-2 font-medium">Selected questions</legend>
              {chosen.fields.map((q, i) => (
                <div key={q.rowKey} className="flex min-w-0 flex-col gap-3 rounded-lg border p-3">
                  <p className="break-words">
                    {i + 1}. {questionNames.get(q.question_id)?.prompt ?? "Selected question"}
                  </p>
                  {questionNames.get(q.question_id)?.archived ? (
                    <p role="alert" className="text-sm text-danger">
                      Archived: replace this question before publishing.
                    </p>
                  ) : null}
                  <FormField
                    label={"Marks for question " + (i + 1)}
                    error={errors.questions?.[i]?.marks?.message}
                    saving={busy}
                  >
                    {(props) => (
                      <Input
                        {...props}
                        inputMode="decimal"
                        {...form.register(("questions." + i + ".marks") as "questions.0.marks")}
                      />
                    )}
                  </FormField>
                  <div>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={busy}
                      onClick={() => chosen.remove(i)}
                    >
                      Remove question {i + 1}
                    </Button>
                  </div>
                </div>
              ))}
              {errors.questions?.root?.message || errors.questions?.message ? (
                <p role="alert" className="text-sm text-danger">
                  {errors.questions.root?.message ?? errors.questions.message}
                </p>
              ) : null}
            </fieldset>
          </>
        )}
        {values.bank_id && available.isPending ? <PageSkeleton rows={1} /> : null}
        {available.error ? (
          <ErrorAlert error={available.error} onRetry={() => void available.refetch()} />
        ) : null}
        {selectedBank.error ? (
          <ErrorAlert error={selectedBank.error} onRetry={() => void selectedBank.refetch()} />
        ) : null}
        <LoadMore
          hasNextPage={available.hasNextPage}
          isFetchingNextPage={available.isFetchingNextPage}
          onClick={() => void available.fetchNextPage()}
        />
        <p className="font-medium tabular-nums">
          Maximum marks: {maximum === null ? "Complete the marks above" : marks(maximum)}
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField label="Pass marks" error={errors.pass_marks?.message} saving={busy}>
            {(props) => <Input {...props} inputMode="decimal" {...form.register("pass_marks")} />}
          </FormField>
          <FormField
            label="Time limit (seconds)"
            help="60 seconds = 1 minute; maximum 24 hours."
            error={errors.time_limit_seconds?.message}
            saving={busy}
          >
            {(props) => (
              <Input {...props} inputMode="numeric" {...form.register("time_limit_seconds")} />
            )}
          </FormField>
          <FormField
            label="Attempts allowed"
            error={errors.attempts_allowed?.message}
            saving={busy}
          >
            {(props) => (
              <Input {...props} inputMode="numeric" {...form.register("attempts_allowed")} />
            )}
          </FormField>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            disabled={busy}
            checked={values.randomize_order}
            onCheckedChange={(on) =>
              form.setValue("randomize_order", on === true, { shouldDirty: true })
            }
          />
          Randomize question order
        </label>
        <FormField label="After submitting, students may see" saving={busy}>
          {(props) => (
            <NativeSelect {...props} {...form.register("reveal_mode")}>
              <option value="score_only">Score only</option>
              <option value="correct_answers">Score and correct answers</option>
              <option value="explanations">Score, correct answers and explanations</option>
            </NativeSelect>
          )}
        </FormField>
        <FormField
          label="Reveal correct answers"
          help="Answer keys and explanations are never shown during an attempt."
          saving={busy}
        >
          {(props) => (
            <NativeSelect {...props} {...form.register("reveal_timing")}>
              <option value="immediately">Immediately after submitting</option>
              <option value="after_attempts_exhausted">After all attempts are exhausted</option>
            </NativeSelect>
          )}
        </FormField>
        {errors.root ? (
          <p role="alert" className="text-sm text-danger">
            {errors.root.message}
          </p>
        ) : null}
        <div>
          <Button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save quiz"}
          </Button>
        </div>
      </form>
    </section>
  );
}
