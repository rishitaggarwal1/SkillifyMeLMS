"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "@/lib/toast";
import { z } from "zod";

import { NativeSelect } from "@/components/native-select";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FormField, PageSkeleton } from "@/components/patterns/states";
import { ErrorAlert, PageTitle } from "@/features/admin/ui";
import { VideoUpload } from "@/features/video/video-upload";
import { api } from "@/lib/api/client";
import {
  LESSON_TYPE_LABELS,
  PLACEHOLDER_LESSON_TYPES,
  type Lesson,
  type Skill,
} from "@/lib/api/types";
import { unwrap } from "@/lib/api/unwrap";

import { allReadyVideosQuery, allSkillsQuery, draftQuery, lessonQuery } from "./api";
import { AssignmentEditor } from "./assignment-editor";
import { uploadFile } from "./files";
import { normalizeNotesDoc } from "./notes-doc";
import { useSetLessonSkills, useUpdateLesson } from "./outline-hooks";
import { SkillsPicker } from "./skills-picker";
import { QuizEditor } from "./quiz-editor";

const NotesEditor = dynamic(() => import("./notes-editor"), {
  ssr: false,
  loading: () => <PageSkeleton />,
});

export function LessonPage({ courseId, lessonId }: { courseId: string; lessonId: string }) {
  const draft = useQuery(draftQuery(courseId)); // holds the revision every save sends
  const lesson = useQuery(lessonQuery(courseId, lessonId));
  if (lesson.isPending || draft.isPending) return <PageSkeleton />;
  if (lesson.error)
    return <ErrorAlert error={lesson.error} onRetry={() => void lesson.refetch()} />;
  if (draft.error) return <ErrorAlert error={draft.error} onRetry={() => void draft.refetch()} />;
  const data = lesson.data;
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Link
          href={`/teach/courses/${courseId}`}
          className="text-sm text-muted-foreground hover:underline"
        >
          ← {draft.data.course.title}
        </Link>
        <PageTitle title={data.title} />
        <div className="flex gap-1.5">
          <Badge variant="outline">{LESSON_TYPE_LABELS[data.lesson_type]}</Badge>
        </div>
      </div>
      <LessonContent courseId={courseId} lesson={data} />
      <LessonSettings courseId={courseId} lesson={data} />
      <LessonSkills courseId={courseId} lesson={data} />
    </div>
  );
}

// ---------------------------------------------------------------------------- settings

const settingsSchema = z.object({
  title: z.string().trim().min(1, "Title is required.").max(200, "At most 200 characters."),
  is_required: z.boolean(),
  estimated_minutes: z
    .string()
    .trim()
    .refine((v) => v === "" || (/^\d+$/.test(v) && +v >= 1 && +v <= 1440), "1 to 1440 minutes."),
  threshold_percent: z
    .string()
    .trim()
    .refine((v) => v === "" || (/^\d+$/.test(v) && +v >= 1 && +v <= 100), "1 to 100 percent."),
});
type Settings = z.infer<typeof settingsSchema>;

function LessonSettings({ courseId, lesson }: { courseId: string; lesson: Lesson }) {
  const update = useUpdateLesson(courseId, lesson.id);
  const placeholder = PLACEHOLDER_LESSON_TYPES.has(lesson.lesson_type);
  const form = useForm<Settings>({
    resolver: zodResolver(settingsSchema),
    values: {
      title: lesson.title,
      is_required: lesson.is_required,
      estimated_minutes: lesson.estimated_minutes ? String(lesson.estimated_minutes) : "",
      threshold_percent: lesson.completion_threshold
        ? String(Math.round(Number(lesson.completion_threshold) * 100))
        : "",
    },
  });
  const isRequired = useWatch({ control: form.control, name: "is_required" });
  const errors = form.formState.errors;

  async function submit(values: Settings) {
    await update.mutateAsync({
      title: values.title,
      ...(placeholder ? {} : { is_required: values.is_required }),
      estimated_minutes: values.estimated_minutes ? Number(values.estimated_minutes) : null,
      ...(lesson.lesson_type === "video"
        ? {
            completion_threshold: values.threshold_percent
              ? (Number(values.threshold_percent) / 100).toFixed(2)
              : null,
          }
        : {}),
    });
    toast.success("Lesson saved");
  }

  return (
    <section aria-label="Lesson settings" className="flex flex-col gap-3">
      <h2 className="text-base font-semibold">Settings</h2>
      <form
        onSubmit={form.handleSubmit(submit)}
        className="flex max-w-xl flex-col gap-4"
        noValidate
      >
        <FormField
          id="lesson-title"
          label="Title"
          error={errors.title?.message}
          saving={form.formState.isSubmitting}
        >
          {(props) => <Input {...props} {...form.register("title")} />}
        </FormField>
        <div className="flex flex-wrap gap-4">
          <div className="flex w-40 flex-col gap-1.5">
            <FormField
              id="lesson-minutes"
              label="Estimated minutes"
              error={errors.estimated_minutes?.message}
              saving={form.formState.isSubmitting}
            >
              {(props) => (
                <Input {...props} inputMode="numeric" {...form.register("estimated_minutes")} />
              )}
            </FormField>
          </div>
          {lesson.lesson_type === "video" ? (
            <div className="flex w-48 flex-col gap-1.5">
              <FormField
                id="lesson-threshold"
                label="Watched to complete (%)"
                error={errors.threshold_percent?.message}
                saving={form.formState.isSubmitting}
              >
                {(props) => (
                  <Input
                    {...props}
                    inputMode="numeric"
                    placeholder="90"
                    {...form.register("threshold_percent")}
                  />
                )}
              </FormField>
            </div>
          ) : null}
        </div>
        {placeholder ? (
          <p className="text-sm text-muted-foreground">
            {LESSON_TYPE_LABELS[lesson.lesson_type]} lessons don&apos;t count toward progress until
            they are available.
          </p>
        ) : (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={isRequired}
              onCheckedChange={(on) =>
                form.setValue("is_required", on === true, { shouldDirty: true })
              }
            />
            Required for course completion
          </label>
        )}
        <div>
          <Button type="submit" disabled={update.isPending || !form.formState.isDirty}>
            {update.isPending ? "Saving…" : "Save settings"}
          </Button>
        </div>
      </form>
    </section>
  );
}

// ---------------------------------------------------------------------------- skills

function LessonSkills({ courseId, lesson }: { courseId: string; lesson: Lesson }) {
  const setSkills = useSetLessonSkills(courseId, lesson.id);
  const taxonomy = useQuery(allSkillsQuery());
  const [picked, setPicked] = useState(new Map<string, Skill>());
  const names = new Map([...(taxonomy.data ?? new Map<string, Skill>()), ...picked]);
  return (
    <section aria-label="Lesson skills" className="flex max-w-xl flex-col gap-2">
      <SkillsPicker
        selected={lesson.skill_ids}
        names={names}
        disabled={setSkills.isPending}
        onChange={(ids, skill) => {
          if (skill) setPicked((m) => new Map(m).set(skill.id, skill));
          setSkills.mutate(ids);
        }}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------- content

function LessonContent({ courseId, lesson }: { courseId: string; lesson: Lesson }) {
  switch (lesson.lesson_type) {
    case "video":
      return <VideoContent courseId={courseId} lesson={lesson} />;
    case "notes":
      return <NotesContent courseId={courseId} lesson={lesson} />;
    case "pdf":
      return <PdfContent courseId={courseId} lesson={lesson} />;
    case "assignment":
      return (
        <AssignmentEditor courseId={courseId} lessonId={lesson.id} lessonTitle={lesson.title} />
      );
    case "quiz":
      return <QuizEditor courseId={courseId} lessonId={lesson.id} lessonTitle={lesson.title} />;
    default:
      return (
        <Alert>
          <AlertTitle>{LESSON_TYPE_LABELS[lesson.lesson_type]} lessons are coming soon</AlertTitle>
          <AlertDescription>
            You can place this lesson in the outline now. Its content is authored in a later
            release, and it doesn&apos;t count toward progress until then.
          </AlertDescription>
        </Alert>
      );
  }
}

function VideoContent({ courseId, lesson }: { courseId: string; lesson: Lesson }) {
  const update = useUpdateLesson(courseId, lesson.id);
  const videos = useQuery(allReadyVideosQuery());
  const [uploading, setUploading] = useState(false);
  const current =
    typeof lesson.content.video_asset_id === "string" ? lesson.content.video_asset_id : "";
  const list = videos.data?.items ?? [];
  const choose = (videoId: string) =>
    update.mutateAsync({ content: videoId ? { video_asset_id: videoId } : {} }).then(
      () => toast.success(videoId ? "Video attached" : "Video removed"),
      () => undefined,
    );

  return (
    <section aria-label="Lesson video" className="flex max-w-xl flex-col gap-3">
      <h2 className="text-base font-semibold">Video</h2>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="lesson-video">Ready videos</Label>
        <NativeSelect
          id="lesson-video"
          value={current}
          disabled={update.isPending}
          onChange={(event) => void choose(event.target.value)}
        >
          <option value="">No video</option>
          {current && !list.some((v) => v.id === current) ? (
            <option value={current}>Current video</option>
          ) : null}
          {list.map((video) => (
            <option key={video.id} value={video.id}>
              {video.title}
              {video.duration_seconds ? ` (${Math.round(video.duration_seconds / 60)} min)` : ""}
            </option>
          ))}
        </NativeSelect>
        {videos.data?.truncated ? (
          <p className="text-xs text-muted-foreground">Showing the first 2,000 videos.</p>
        ) : null}
      </div>
      {uploading ? (
        <div className="rounded-lg border p-3">
          <VideoUpload
            onReady={(videoId) => {
              setUploading(false);
              void videos.refetch();
              void choose(videoId);
            }}
          />
        </div>
      ) : (
        <div>
          <Button variant="outline" onClick={() => setUploading(true)}>
            Upload a new video
          </Button>
        </div>
      )}
    </section>
  );
}

function PdfContent({ courseId, lesson }: { courseId: string; lesson: Lesson }) {
  const update = useUpdateLesson(courseId, lesson.id);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileId = typeof lesson.content.file_id === "string" ? lesson.content.file_id : null;
  const file = useQuery({
    queryKey: ["files", fileId],
    enabled: !!fileId,
    queryFn: () =>
      unwrap(api.GET("/api/v1/files/{file_id}", { params: { path: { file_id: fileId! } } })),
  });

  async function upload(selected: File) {
    setError(null);
    setProgress(0);
    try {
      const stored = await uploadFile(selected, "pdf", setProgress);
      await update.mutateAsync({ content: { file_id: stored.id } });
      toast.success("PDF attached");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setProgress(null);
    }
  }

  async function openPdf() {
    if (!fileId) return;
    const download = await unwrap(
      api.GET("/api/v1/files/{file_id}/download", { params: { path: { file_id: fileId } } }),
    );
    window.open(download.url, "_blank", "noopener,noreferrer");
  }

  return (
    <section aria-label="Lesson PDF" className="flex max-w-xl flex-col gap-3">
      <h2 className="text-base font-semibold">PDF</h2>
      {fileId ? (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium">{file.data?.file_name ?? "Attached PDF"}</span>
          {file.data?.size_bytes ? (
            <span className="text-muted-foreground">
              {(file.data.size_bytes / 1024 ** 2).toFixed(1)} MB
            </span>
          ) : null}
          <Button size="sm" variant="outline" onClick={() => void openPdf()}>
            Open
          </Button>
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">No PDF attached yet.</p>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="lesson-pdf">{fileId ? "Replace with another PDF" : "Upload a PDF"}</Label>
        <Input
          id="lesson-pdf"
          type="file"
          accept="application/pdf"
          disabled={progress !== null}
          onChange={(event) => {
            const selected = event.target.files?.[0];
            if (selected) void upload(selected);
            event.target.value = "";
          }}
        />
      </div>
      {progress !== null ? (
        <progress aria-label="Upload progress" max={1} value={progress} className="w-full" />
      ) : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function NotesContent({ courseId, lesson }: { courseId: string; lesson: Lesson }) {
  const update = useUpdateLesson(courseId, lesson.id);
  // Signed URLs for the images already in the saved draft (short-lived, fetched on open).
  const preview = useQuery({
    queryKey: ["notes-preview", courseId, lesson.id],
    staleTime: 60_000,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/courses/{course_id}/lessons/{lesson_id}/preview", {
          params: { path: { course_id: courseId, lesson_id: lesson.id } },
        }),
      ),
  });
  if (preview.isPending) return <PageSkeleton />;
  return (
    <section aria-label="Lesson notes" className="flex flex-col gap-2">
      <h2 className="text-base font-semibold">Notes</h2>
      <NotesEditor
        initialDoc={normalizeNotesDoc(lesson.content.doc ?? { type: "doc", content: [] })}
        initialImageUrls={preview.data?.image_urls ?? {}}
        saving={update.isPending}
        onSave={async (doc) => {
          await update.mutateAsync({ content: { doc } });
          toast.success("Notes saved");
        }}
      />
    </section>
  );
}
