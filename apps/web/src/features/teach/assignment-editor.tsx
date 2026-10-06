"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { FormField, PageSkeleton } from "@/components/patterns/states";
import { ErrorAlert } from "@/features/admin/ui";
import type { AssignmentDraft } from "@/lib/api/types";
import { istInputToIso, isoToIstInput } from "@/lib/ist";

import { assignmentDraftQuery, useSaveAssignment, type AssignmentFields } from "./assignment-api";
import { normalizeNotesDoc, type NotesDoc } from "./notes-doc";

const NotesEditor = dynamic(() => import("./notes-editor"), {
  ssr: false,
  loading: () => <PageSkeleton />,
});

const KINDS = [
  { value: "text", label: "Typed answer" },
  { value: "file", label: "File upload (PDF, PNG or JPEG)" },
] as const;

export const assignmentFormSchema = z.object({
  title: z.string().trim().min(1, "Title is required.").max(200, "At most 200 characters."),
  due: z.string(),
  max_marks: z
    .string()
    .trim()
    .refine((v) => /^\d+$/.test(v) && +v >= 1 && +v <= 1000, "A whole number from 1 to 1000."),
  submission_kinds: z.array(z.enum(["text", "file"])).min(1, "Allow at least one kind."),
});
type AssignmentForm = z.infer<typeof assignmentFormSchema>;

function toFields(values: AssignmentForm, instructions: NotesDoc | null): AssignmentFields {
  return {
    title: values.title,
    instructions,
    due_at: istInputToIso(values.due),
    max_marks: Number(values.max_marks),
    submission_kinds: [...values.submission_kinds].sort(),
  };
}

function formValues(draft: AssignmentDraft | null, lessonTitle: string): AssignmentForm {
  return {
    title: draft?.title ?? lessonTitle,
    due: isoToIstInput(draft?.due_at),
    max_marks: String(draft?.max_marks ?? 10),
    submission_kinds: draft?.submission_kinds ?? ["text", "file"],
  };
}

/** An assignment lesson's details and instructions (published with the next version). */
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
      key={draft.data?.updated_at ?? "new"}
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
  const save = useSaveAssignment(courseId, lessonId);
  const form = useForm<AssignmentForm>({
    resolver: zodResolver(assignmentFormSchema),
    defaultValues: formValues(draft, lessonTitle),
  });
  const errors = form.formState.errors;
  const instructions = (draft?.instructions as NotesDoc | null | undefined) ?? null;

  async function submit(values: AssignmentForm) {
    await save.mutateAsync(toFields(values, instructions));
    toast.success("Assignment saved");
  }

  return (
    <section aria-label="Assignment" className="flex flex-col gap-5">
      <div className="flex flex-col gap-3">
        <h2 className="text-base font-semibold">Assignment</h2>
        {draft ? null : (
          <p className="text-sm text-muted-foreground">
            Save the details to make this lesson publishable.
          </p>
        )}
        <form
          onSubmit={form.handleSubmit(submit)}
          className="flex max-w-xl flex-col gap-4"
          aria-label="Assignment details"
          noValidate
        >
          <FormField
            id="assignment-title"
            label="Title students see"
            error={errors.title?.message}
            saving={form.formState.isSubmitting}
          >
            {(props) => <Input {...props} {...form.register("title")} />}
          </FormField>
          <div className="flex flex-wrap gap-4">
            <div className="flex w-36 flex-col gap-1.5">
              <FormField
                id="assignment-marks"
                label="Maximum marks"
                error={errors.max_marks?.message}
                saving={form.formState.isSubmitting}
              >
                {(props) => (
                  <Input {...props} inputMode="numeric" {...form.register("max_marks")} />
                )}
              </FormField>
            </div>
            <div className="flex w-60 flex-col gap-1.5">
              <FormField
                id="assignment-due"
                label="Due (IST, optional)"
                saving={form.formState.isSubmitting}
              >
                {(props) => <Input {...props} type="datetime-local" {...form.register("due")} />}
              </FormField>
            </div>
          </div>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Students may submit</legend>
            <Controller
              control={form.control}
              name="submission_kinds"
              render={({ field }) => (
                <>
                  {KINDS.map((kind) => (
                    <label key={kind.value} className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={field.value.includes(kind.value)}
                        onCheckedChange={(on) =>
                          field.onChange(
                            on === true
                              ? [...new Set([...field.value, kind.value])]
                              : field.value.filter((k) => k !== kind.value),
                          )
                        }
                      />
                      {kind.label}
                    </label>
                  ))}
                </>
              )}
            />
            {errors.submission_kinds ? (
              <p className="text-sm text-destructive">{errors.submission_kinds.message}</p>
            ) : null}
            <p className="text-xs text-muted-foreground">
              Changing the marks or what students may submit needs a major version: existing grades
              depend on them.
            </p>
          </fieldset>
          <div>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save details"}
            </Button>
          </div>
        </form>
      </div>
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">Instructions</h3>
        {draft ? (
          <NotesEditor
            initialDoc={normalizeNotesDoc(instructions ?? { type: "doc", content: [] })}
            initialImageUrls={{}}
            label="Instructions"
            saveLabel="Save instructions"
            allowImages={false}
            saving={save.isPending}
            onSave={async (doc) => {
              await save.mutateAsync(toFields(form.getValues(), doc));
              toast.success("Instructions saved");
            }}
          />
        ) : (
          <p className="text-sm text-muted-foreground">Save the details first.</p>
        )}
      </div>
    </section>
  );
}
