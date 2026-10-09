"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useState } from "react";
import { Controller, useFieldArray, useForm, useWatch } from "react-hook-form";
import { toast } from "@/lib/toast";
import { z } from "zod";
import { NativeSelect } from "@/components/native-select";
import { PublishedContent } from "@/components/patterns/published-content";
import { FormField, LiveAnnouncement, PageSkeleton } from "@/components/patterns/states";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ErrorAlert, errorMessage } from "@/features/admin/ui";
import type { AssignmentDraft } from "@/lib/api/types";
import { istInputToIso, isoToIstInput } from "@/lib/ist";
import { cents } from "@/lib/marks";
import {
  assignmentDraftQuery,
  assignmentPreviewQuery,
  useSaveAssignment,
  type AssignmentFields,
} from "./assignment-api";
import { normalizeNotesDoc, type NotesDoc } from "./notes-doc";

const NotesEditor = dynamic(() => import("./notes-editor"), {
  ssr: false,
  loading: () => <PageSkeleton />,
});
export const assignmentFormSchema = z
  .object({
    title: z.string().trim().min(1, "Title is required.").max(200, "At most 200 characters."),
    due: z.string(),
    max_marks: z
      .string()
      .trim()
      .refine((v) => /^\d+$/.test(v) && +v >= 1 && +v <= 1000, "A whole number from 1 to 1000."),
    submission_kinds: z.array(z.enum(["text", "file"])).min(1, "Allow at least one kind."),
    rubric_enabled: z.boolean(),
    criteria: z
      .array(
        z.object({
          id: z.string(),
          label: z.string(),
          description: z.string(),
          max_marks: z.string(),
        }),
      )
      .max(50),
    late_mode: z.enum(["accept", "reject", "penalty"]),
    penalty_rate: z.string(),
  })
  .superRefine((v, ctx) => {
    const fail = (path: (string | number)[], message: string) =>
      ctx.addIssue({ code: "custom", path, message });
    if (v.due && Number.isNaN(new Date(istInputToIso(v.due) ?? "").getTime()))
      fail(["due"], "Enter a valid due date.");
    if (v.rubric_enabled) {
      if (!v.criteria.length) fail(["criteria"], "Add at least one criterion.");
      if (new Set(v.criteria.map((c) => c.id)).size !== v.criteria.length)
        fail(["criteria"], "Criterion IDs must be unique.");
      let sum = 0n;
      v.criteria.forEach((c, i) => {
        if (!/^[a-zA-Z0-9_-]{1,64}$/.test(c.id))
          fail(["criteria", i, "id"], "A stable criterion ID is required.");
        if (!c.label.trim() || c.label.trim().length > 200)
          fail(["criteria", i, "label"], "A label from 1 to 200 characters.");
        if (c.description.length > 2000)
          fail(["criteria", i, "description"], "At most 2000 characters.");
        const value = cents(c.max_marks.trim());
        if (value === null || value <= 0n || value > 100000n)
          fail(
            ["criteria", i, "max_marks"],
            "Positive marks up to 1000, with at most two decimals.",
          );
        else sum += value;
      });
      if (sum !== cents(v.max_marks))
        fail(["criteria"], "Criterion marks must total the assignment's maximum marks.");
    }
    if (v.late_mode === "penalty") {
      if (!v.due) fail(["due"], "A penalty requires a due date.");
      const rate = cents(v.penalty_rate.trim());
      if (rate === null || rate <= 0n || rate > 10000n)
        fail(["penalty_rate"], "A percentage greater than 0 and at most 100, with two decimals.");
    }
  });
export type AssignmentFormValues = z.infer<typeof assignmentFormSchema>;
export function assignmentFields(
  values: AssignmentFormValues,
  instructions: NotesDoc | null,
): AssignmentFields {
  return {
    title: values.title,
    instructions,
    due_at: istInputToIso(values.due),
    max_marks: Number(values.max_marks),
    submission_kinds: [...values.submission_kinds].sort(),
    rubric: values.rubric_enabled
      ? {
          criteria: values.criteria.map((c) => ({
            ...c,
            label: c.label.trim(),
            max_marks: c.max_marks.trim(),
          })),
        }
      : null,
    late_policy: {
      mode: values.late_mode,
      percent_per_day: values.late_mode === "penalty" ? values.penalty_rate.trim() : null,
    },
  };
}
function initial(draft: AssignmentDraft | null, title: string): AssignmentFormValues {
  return {
    title: draft?.title ?? title,
    due: isoToIstInput(draft?.due_at),
    max_marks: String(draft?.max_marks ?? 10),
    submission_kinds: draft?.submission_kinds ?? ["text", "file"],
    rubric_enabled: !!draft?.rubric,
    criteria:
      draft?.rubric?.criteria.map((c) => ({
        ...c,
        description: c.description ?? "",
        max_marks: String(c.max_marks),
      })) ?? [],
    late_mode: draft?.late_policy?.mode ?? "accept",
    penalty_rate: String(draft?.late_policy?.percent_per_day ?? ""),
  };
}
export function AssignmentEditor({
  courseId,
  lessonId,
  lessonTitle,
}: {
  courseId: string;
  lessonId: string;
  lessonTitle: string;
}) {
  const draft = useQuery(assignmentDraftQuery(courseId, lessonId));
  if (draft.isPending) return <PageSkeleton />;
  if (draft.error) return <ErrorAlert error={draft.error} onRetry={() => void draft.refetch()} />;
  return (
    <AssignmentEditorForm
      key={lessonId}
      courseId={courseId}
      lessonId={lessonId}
      lessonTitle={lessonTitle}
      draft={draft.data ?? null}
    />
  );
}
function AssignmentEditorForm({
  courseId,
  lessonId,
  lessonTitle,
  draft,
}: {
  courseId: string;
  lessonId: string;
  lessonTitle: string;
  draft: AssignmentDraft | null;
}) {
  const qc = useQueryClient();
  const save = useSaveAssignment(courseId, lessonId);
  const [instructions, setInstructions] = useState<NotesDoc | null>(
    (draft?.instructions as NotesDoc | null | undefined) ?? null,
  );
  const [savedOnce, setSavedOnce] = useState(!!draft);
  const preview = useQuery({ ...assignmentPreviewQuery(courseId, lessonId), enabled: savedOnce });
  const form = useForm<AssignmentFormValues>({
    resolver: zodResolver(assignmentFormSchema),
    defaultValues: initial(draft, lessonTitle),
  });
  const criteria = useFieldArray({ control: form.control, name: "criteria", keyName: "rowKey" });
  const rubricEnabled = useWatch({ control: form.control, name: "rubric_enabled" });
  const lateMode = useWatch({ control: form.control, name: "late_mode" });
  const errors = form.formState.errors;
  const saving = save.isPending || form.formState.isSubmitting;
  async function persist(
    values: AssignmentFormValues,
    doc: NotesDoc | null,
    message = "Assignment saved",
  ) {
    const saved = await save.mutateAsync(assignmentFields(values, doc));
    form.reset(initial(saved, lessonTitle));
    setSavedOnce(true);
    void qc.invalidateQueries({ queryKey: assignmentPreviewQuery(courseId, lessonId).queryKey });
    toast.success(message);
  }
  async function submit(values: AssignmentFormValues) {
    try {
      await persist(values, instructions);
    } catch (error) {
      form.setError("root", { message: errorMessage(error) + " Your changes are still here." });
    }
  }
  return (
    <section aria-label="Assignment" className="flex flex-col gap-5">
      <h2 className="text-base font-semibold">Assignment</h2>
      {!savedOnce ? (
        <p className="text-sm text-muted-foreground">
          Save the details to make this lesson publishable.
        </p>
      ) : null}
      <form
        onSubmit={form.handleSubmit(submit)}
        className="flex max-w-2xl flex-col gap-4"
        aria-label="Assignment details"
        noValidate
      >
        <FormField label="Title students see" error={errors.title?.message} saving={saving}>
          {(props) => <Input {...props} {...form.register("title")} />}
        </FormField>
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField label="Maximum marks" error={errors.max_marks?.message} saving={saving}>
            {(props) => <Input {...props} inputMode="numeric" {...form.register("max_marks")} />}
          </FormField>
          <FormField label="Due (IST, optional)" error={errors.due?.message} saving={saving}>
            {(props) => <Input {...props} type="datetime-local" {...form.register("due")} />}
          </FormField>
        </div>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium">Students may submit</legend>
          <Controller
            control={form.control}
            name="submission_kinds"
            render={({ field }) => (
              <>
                {(["text", "file"] as const).map((kind) => (
                  <label key={kind} className="flex min-h-11 items-center gap-2 text-sm">
                    <Checkbox
                      disabled={saving}
                      checked={field.value.includes(kind)}
                      onCheckedChange={(on) =>
                        field.onChange(
                          on === true
                            ? [...new Set([...field.value, kind])]
                            : field.value.filter((k) => k !== kind),
                        )
                      }
                    />
                    {kind === "text" ? "Typed answer" : "File upload (PDF, PNG or JPEG)"}
                  </label>
                ))}
              </>
            )}
          />
          {errors.submission_kinds ? (
            <p role="alert" className="text-sm text-destructive">
              {errors.submission_kinds.message}
            </p>
          ) : null}
        </fieldset>
        <section aria-label="Rubric" className="flex flex-col gap-3 rounded-lg border p-4">
          <Controller
            control={form.control}
            name="rubric_enabled"
            render={({ field }) => (
              <label className="flex min-h-11 items-center gap-2 font-medium">
                <Checkbox
                  disabled={saving}
                  checked={field.value}
                  onCheckedChange={(on) => field.onChange(on === true)}
                />{" "}
                Grade with a rubric
              </label>
            )}
          />
          {rubricEnabled ? (
            <>
              {criteria.fields.map((criterion, i) => (
                <fieldset
                  key={criterion.rowKey}
                  className="flex flex-col gap-3 rounded-md border p-3"
                >
                  <legend className="text-sm font-medium">Criterion {i + 1}</legend>
                  <FormField
                    label={"Criterion " + (i + 1) + " label"}
                    error={errors.criteria?.[i]?.label?.message}
                    saving={saving}
                  >
                    {(props) => (
                      <Input
                        {...props}
                        {...form.register(("criteria." + i + ".label") as "criteria.0.label")}
                      />
                    )}
                  </FormField>
                  <FormField
                    label={"Criterion " + (i + 1) + " description"}
                    error={errors.criteria?.[i]?.description?.message}
                    saving={saving}
                  >
                    {(props) => (
                      <Textarea
                        {...props}
                        rows={2}
                        {...form.register(
                          ("criteria." + i + ".description") as "criteria.0.description",
                        )}
                      />
                    )}
                  </FormField>
                  <FormField
                    label={"Criterion " + (i + 1) + " maximum marks"}
                    error={errors.criteria?.[i]?.max_marks?.message}
                    saving={saving}
                  >
                    {(props) => (
                      <Input
                        {...props}
                        inputMode="decimal"
                        {...form.register(
                          ("criteria." + i + ".max_marks") as "criteria.0.max_marks",
                        )}
                      />
                    )}
                  </FormField>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={saving}
                    onClick={() => criteria.remove(i)}
                  >
                    Remove criterion {i + 1}
                  </Button>
                </fieldset>
              ))}
              <Button
                type="button"
                variant="outline"
                disabled={saving || criteria.fields.length >= 50}
                onClick={() =>
                  criteria.append({
                    id: crypto.randomUUID(),
                    label: "",
                    description: "",
                    max_marks: "",
                  })
                }
              >
                Add criterion
              </Button>
              {errors.criteria?.root?.message || errors.criteria?.message ? (
                <p role="alert" className="text-sm text-destructive">
                  {errors.criteria.root?.message ?? errors.criteria.message}
                </p>
              ) : null}
              <p className="text-sm text-muted-foreground">
                Criterion marks must total the maximum. Renaming a criterion keeps its identity;
                replacing or changing its maximum requires a major version.
              </p>
            </>
          ) : null}
        </section>
        <FormField label="Late submissions" saving={saving}>
          {(props) => (
            <NativeSelect {...props} {...form.register("late_mode")}>
              <option value="accept">Accept without penalty</option>
              <option value="reject">Reject after due date</option>
              <option value="penalty">Penalty per day</option>
            </NativeSelect>
          )}
        </FormField>
        {lateMode === "penalty" ? (
          <FormField
            label="Penalty percent per day"
            help="Applied to earned marks for each started 24-hour period after the deadline, capped at 100%. Frozen when the student submits."
            error={errors.penalty_rate?.message}
            saving={saving}
          >
            {(props) => <Input {...props} inputMode="decimal" {...form.register("penalty_rate")} />}
          </FormField>
        ) : null}
        <p className="text-sm text-muted-foreground">
          Maximum marks, submission kinds and rubric criteria affect existing grading and require a
          major version. Late-policy changes apply to future submissions.
        </p>
        {errors.root ? (
          <p role="alert" className="text-sm text-destructive">
            {errors.root.message}
          </p>
        ) : null}
        <LiveAnnouncement>{saving ? "Saving assignment" : ""}</LiveAnnouncement>
        <div>
          <Button type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save details"}
          </Button>
        </div>
      </form>
      <section aria-label="Instructions editor" className="flex flex-col gap-2">
        <h3 className="text-base font-semibold">Instructions</h3>
        {savedOnce ? (
          <>
            {preview.isPending ? (
              <PageSkeleton />
            ) : preview.error ? (
              <ErrorAlert error={preview.error} onRetry={() => void preview.refetch()} />
            ) : (
              <NotesEditor
                initialDoc={normalizeNotesDoc(instructions ?? { type: "doc", content: [] })}
                initialImageUrls={preview.data.image_urls}
                label="Instructions"
                saveLabel="Save instructions"
                saving={saving}
                onChange={setInstructions}
                onSave={async (doc) => {
                  if (!(await form.trigger()))
                    throw new Error(
                      "Correct the assignment details above before saving instructions.",
                    );
                  setInstructions(doc);
                  await persist(form.getValues(), doc, "Instructions saved");
                }}
              />
            )}
            {preview.data ? (
              <details className="rounded-lg border p-4">
                <summary className="min-h-11 cursor-pointer font-medium">
                  Saved instructions preview
                </summary>
                <PublishedContent html={preview.data.html} imageUrls={preview.data.image_urls} />
              </details>
            ) : null}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Save the details first.</p>
        )}
      </section>
    </section>
  );
}
