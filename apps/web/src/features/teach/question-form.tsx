"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useFieldArray, useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { NativeSelect } from "@/components/native-select";
import { FormField, LiveAnnouncement } from "@/components/patterns/states";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/components/patterns/page";
import { ApiError } from "@/lib/api/errors";
import type { AuthorQuestion, QuestionBody } from "@/lib/api/types";

export const QUESTION_TYPES = {
  mcq_single: "Single choice",
  mcq_multi: "Multiple choice",
  fill_blank: "Fill in the blank",
};

export const questionFormSchema = z
  .object({
    question_type: z.enum(["mcq_single", "mcq_multi", "fill_blank"]),
    prompt: z.string().trim().min(1, "Write the question.").max(20000),
    options: z.array(z.object({ id: z.string(), text: z.string(), correct: z.boolean() })).max(100),
    accepted: z.string(),
    case_sensitive: z.boolean(),
    explanation: z.string().max(20000),
  })
  .superRefine((value, ctx) => {
    const issue = (path: (string | number)[], message: string) =>
      ctx.addIssue({ code: "custom", path, message });
    if (value.question_type === "fill_blank") {
      const answers = value.accepted
        .split("\n")
        .map((v) => v.normalize("NFC").trim())
        .filter(Boolean);
      const comparable = value.case_sensitive ? answers : answers.map((v) => v.toLowerCase());
      if (!answers.length || answers.length > 100 || answers.some((v) => v.length > 20000))
        issue(["accepted"], "Enter 1 to 100 accepted answers, one per line.");
      if (new Set(comparable).size !== comparable.length)
        issue(["accepted"], "Accepted answers must be distinct.");
      return;
    }
    if (value.options.length < 2) issue(["options"], "Add at least two options.");
    if (new Set(value.options.map((v) => v.id)).size !== value.options.length)
      issue(["options"], "Option identities must be distinct.");
    value.options.forEach((v, i) => {
      if (!v.text.trim() || v.text.length > 20000)
        issue(["options", i, "text"], "Write the option (at most 20,000 characters).");
    });
    const correct = value.options.filter((v) => v.correct).length;
    if (
      !correct ||
      correct === value.options.length ||
      (value.question_type === "mcq_single" && correct !== 1)
    ) {
      issue(["options"], "Choose the correct option(s) and leave at least one incorrect option.");
    }
  });
export type QuestionFormValues = z.infer<typeof questionFormSchema>;
export function questionFields(values: QuestionFormValues): QuestionBody {
  const blank = values.question_type === "fill_blank";
  return {
    question_type: values.question_type,
    prompt: values.prompt,
    explanation: values.explanation,
    options: blank ? [] : values.options.map(({ id, text }) => ({ id, text: text.trim() })),
    answer_key: {
      correct_option_ids: blank ? [] : values.options.filter((v) => v.correct).map((v) => v.id),
      accepted_answers: blank
        ? values.accepted
            .split("\n")
            .map((v) => v.normalize("NFC").trim())
            .filter(Boolean)
        : [],
      case_sensitive: blank && values.case_sensitive,
    },
  };
}
function initial(question?: AuthorQuestion): QuestionFormValues {
  return {
    question_type: question?.question_type ?? "mcq_single",
    prompt: question?.prompt ?? "",
    options: question?.options?.length
      ? question.options.map((v) => ({
          ...v,
          correct: question.answer_key.correct_option_ids?.includes(v.id) ?? false,
        }))
      : [
          { id: crypto.randomUUID(), text: "", correct: true },
          { id: crypto.randomUUID(), text: "", correct: false },
        ],
    accepted: question?.answer_key.accepted_answers?.join("\n") ?? "",
    case_sensitive: question?.answer_key.case_sensitive ?? false,
    explanation: question?.explanation ?? "",
  };
}

export function QuestionForm({
  question,
  save,
}: {
  question?: AuthorQuestion;
  save: (body: QuestionBody) => Promise<AuthorQuestion>;
}) {
  const form = useForm<QuestionFormValues>({
    resolver: zodResolver(questionFormSchema),
    defaultValues: initial(question),
  });
  const options = useFieldArray({ control: form.control, name: "options", keyName: "rowKey" });
  const values = useWatch({ control: form.control });
  const errors = form.formState.errors;
  const busy = form.formState.isSubmitting;
  async function submit(input: QuestionFormValues) {
    try {
      const saved = await save(questionFields(input));
      form.reset(initial(saved));
    } catch (error) {
      form.setError("root", {
        message:
          error instanceof ApiError && error.status === 409
            ? "This bank changed elsewhere. Your edits are kept; review the latest bank before saving again."
            : errorMessage(error),
      });
    }
  }
  return (
    <form
      aria-label="Question details"
      onSubmit={form.handleSubmit(submit)}
      noValidate
      className="flex flex-col gap-4"
    >
      <FormField label="Question type" saving={busy}>
        {(props) => (
          <NativeSelect {...props} {...form.register("question_type")}>
            {Object.entries(QUESTION_TYPES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </NativeSelect>
        )}
      </FormField>
      <FormField label="Question" error={errors.prompt?.message} saving={busy}>
        {(props) => <Textarea {...props} rows={3} {...form.register("prompt")} />}
      </FormField>
      {values.question_type === "fill_blank" ? (
        <>
          <FormField
            label="Accepted answers (one per line)"
            error={errors.accepted?.message}
            saving={busy}
          >
            {(props) => <Textarea {...props} rows={3} {...form.register("accepted")} />}
          </FormField>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={values.case_sensitive}
              disabled={busy}
              onCheckedChange={(on) =>
                form.setValue("case_sensitive", on === true, { shouldDirty: true })
              }
            />
            Match case exactly
          </label>
        </>
      ) : (
        <fieldset className="flex min-w-0 flex-col gap-3">
          <legend className="mb-2 font-medium">Options and correct answers</legend>
          {options.fields.map((option, i) => (
            <div key={option.rowKey} className="flex min-w-0 flex-col gap-2 rounded-lg border p-3">
              <FormField
                label={"Option " + (i + 1)}
                error={errors.options?.[i]?.text?.message}
                saving={busy}
              >
                {(props) => (
                  <Input
                    {...props}
                    {...form.register(("options." + i + ".text") as "options.0.text")}
                  />
                )}
              </FormField>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type={values.question_type === "mcq_single" ? "radio" : "checkbox"}
                    name="correct-option"
                    checked={values.options?.[i]?.correct ?? false}
                    disabled={busy}
                    onChange={(event) => {
                      if (values.question_type === "mcq_single")
                        options.fields.forEach((_v, index) =>
                          form.setValue(
                            ("options." + index + ".correct") as "options.0.correct",
                            index === i,
                            { shouldDirty: true },
                          ),
                        );
                      else
                        form.setValue(
                          ("options." + i + ".correct") as "options.0.correct",
                          event.target.checked,
                          { shouldDirty: true },
                        );
                    }}
                  />
                  {"Option " + (i + 1) + " is correct"}
                </label>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={busy || options.fields.length <= 2}
                  onClick={() => options.remove(i)}
                >
                  Remove option {i + 1}
                </Button>
              </div>
            </div>
          ))}
          {errors.options?.root?.message || errors.options?.message ? (
            <p role="alert" className="text-sm text-danger">
              {errors.options.root?.message ?? errors.options.message}
            </p>
          ) : null}
          <div>
            <Button
              type="button"
              variant="outline"
              disabled={busy || options.fields.length >= 100}
              onClick={() => options.append({ id: crypto.randomUUID(), text: "", correct: false })}
            >
              Add option
            </Button>
          </div>
          {values.question_type === "mcq_multi" ? (
            <p className="text-sm text-muted-foreground">
              Partial marks are available. Incorrect selections reduce earned marks, down to zero.
            </p>
          ) : null}
        </fieldset>
      )}
      <FormField
        label="Explanation (optional)"
        help="Shown only according to the quiz's result settings."
        error={errors.explanation?.message}
        saving={busy}
      >
        {(props) => <Textarea {...props} rows={3} {...form.register("explanation")} />}
      </FormField>
      {errors.root ? (
        <p role="alert" className="text-sm text-danger">
          {errors.root.message}
        </p>
      ) : null}
      <LiveAnnouncement>{busy ? "Saving question" : ""}</LiveAnnouncement>
      <div>
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save question"}
        </Button>
      </div>
    </form>
  );
}
