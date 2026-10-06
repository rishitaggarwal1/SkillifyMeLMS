"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { PageSkeleton } from "@/components/patterns/states";
import { Textarea } from "@/components/ui/textarea";
import { ErrorAlert } from "@/features/admin/ui";
import type { Course, PublishPreview } from "@/lib/api/types";

import { draftQuery, publishPreviewQuery, usePublish } from "./api";

const BLOCKERS: Record<PublishPreview["blockers"][number]["code"], string> = {
  empty_course: "Add at least one lesson.",
  video_not_ready: "These video lessons need a processed video:",
  pdf_not_ready: "These PDF lessons need an uploaded PDF:",
  quiz_not_ready: "These quiz lessons need a complete definition and active questions:",
  assignment_not_ready: "These assignment lessons need their details saved:",
  course_archived: "Archived courses can't be published.",
};

const CHANGES: Record<PublishPreview["structural_changes"][number]["code"], string> = {
  quiz_structure_changed: "Quiz questions, marks or attempt rules changed",
  quiz_grading_changed: "Quiz grading rules changed",
  assignment_structure_changed: "Assignment marks, submission kinds or rubric criteria changed",
  modules_changed: "Modules were added, removed or reordered",
  lessons_added: "Lessons were added",
  lessons_removed: "Lessons were removed",
  lessons_reordered: "Lessons were reordered or moved between modules",
  lesson_settings_changed: "A lesson's type, required flag or completion threshold changed",
};

export function PublishDialog({ course, onClose }: { course: Course; onClose: () => void }) {
  const preview = useQuery({ ...publishPreviewQuery(course.id), staleTime: 0 });
  const draft = useQuery(draftQuery(course.id));
  const publish = usePublish(course.id);
  const [releaseType, setReleaseType] = useState<"major" | "minor" | null>(null);
  const [notes, setNotes] = useState("");

  const titleOf = (id: string) =>
    draft.data?.modules.flatMap((m) => m.lessons).find((l) => l.id === id)?.title ?? "Lesson";
  const data = preview.data;
  const minorAllowed = !!data && data.minor_allowed && !data.is_first_release;
  const chosen = releaseType ?? (minorAllowed ? "minor" : "major");
  const blocked = !data || data.blockers.length > 0;

  async function submit() {
    try {
      const version = await publish.mutateAsync({ release_type: chosen, release_notes: notes });
      toast.success(`Published v${version.version}`);
      onClose();
    } catch {
      // Shown below from publish.error.
    }
  }

  return (
    <Dialog open onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Publish {course.title}</DialogTitle>
          <DialogDescription>
            Students see a published version; your later edits stay in the draft until the next
            publish.
          </DialogDescription>
        </DialogHeader>
        {preview.isPending ? <PageSkeleton /> : null}
        {preview.error ? (
          <ErrorAlert error={preview.error} onRetry={() => void preview.refetch()} />
        ) : null}
        {data ? (
          <div className="flex flex-col gap-4 text-sm">
            {data.blockers.length ? (
              <div role="alert" className="rounded-md border border-destructive/40 p-3">
                <p className="font-medium text-destructive">Fix these before publishing:</p>
                <ul className="mt-1 list-disc pl-5">
                  {data.blockers.map((b) => (
                    <li key={b.code}>
                      {BLOCKERS[b.code]}
                      {b.lesson_ids?.length ? ` ${b.lesson_ids.map(titleOf).join(", ")}` : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {data.is_first_release ? (
              <p>This is the first release: it will be version {data.next_major}.</p>
            ) : (
              <section aria-label="Changes since the last version">
                <p className="font-medium">Changes since the last version</p>
                {data.structural_changes.length ? (
                  <ul className="mt-1 list-disc pl-5">
                    {data.structural_changes.map((c) => (
                      <li key={c.code}>
                        {CHANGES[c.code]}
                        {c.lesson_ids?.length ? `: ${c.lesson_ids.map(titleOf).join(", ")}` : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-muted-foreground">
                    No structural changes: content corrections only.
                  </p>
                )}
              </section>
            )}
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-1 font-medium">Release type</legend>
              <label className="flex items-start gap-2">
                <input
                  type="radio"
                  name="release-type"
                  className="mt-1"
                  checked={chosen === "minor"}
                  disabled={!minorAllowed}
                  onChange={() => setReleaseType("minor")}
                />
                <span>
                  Minor {data.next_minor ? `(v${data.next_minor})` : null}: corrections. Reaches
                  every enrolled student automatically.
                  {!minorAllowed && !data.is_first_release ? (
                    <span className="block text-muted-foreground">
                      Not available: the structure changed.
                    </span>
                  ) : null}
                </span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="radio"
                  name="release-type"
                  className="mt-1"
                  checked={chosen === "major"}
                  onChange={() => setReleaseType("major")}
                />
                <span>
                  Major (v{data.next_major}): new enrollments get it; organization admins can move
                  existing enrollments over.
                </span>
              </label>
            </fieldset>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="release-notes">Release notes (optional)</Label>
              <Textarea
                id="release-notes"
                rows={3}
                maxLength={2000}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </div>
          </div>
        ) : null}
        {publish.error ? <ErrorAlert error={publish.error} /> : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={blocked || publish.isPending}>
            {publish.isPending ? "Publishing…" : "Publish"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
