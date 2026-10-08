"use client";

import "@/styles/notes-code.css";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { PageSkeleton } from "@/components/patterns/states";
import { errorMessage } from "@/features/admin/ui";
import { EnrollmentVideo } from "@/features/video/enrollment-video";

import {
  enrollmentQuery,
  learnKeys,
  notesImagesQuery,
  useCompleteLesson,
  usePdfAccess,
} from "./api";
import { PLACEHOLDER_TYPES, type LessonProgress, type OutlineLesson } from "./outline";

type Props = { enrollmentId: string; lesson: OutlineLesson; progress?: LessonProgress };

const QuizLesson = dynamic(() => import("./quiz-lesson"), {
  loading: () => <PageSkeleton kind="form" />,
});
const AssignmentLesson = dynamic(
  () => import("./assignment-lesson").then((mod) => mod.AssignmentLesson),
  {
    loading: () => <PageSkeleton kind="form" />,
  },
);

export function LessonView(props: Props) {
  if (PLACEHOLDER_TYPES.has(props.lesson.lesson_type)) return <PlaceholderLesson />;
  switch (props.lesson.lesson_type) {
    case "video":
      return <VideoLesson {...props} />;
    case "notes":
      return <NotesLesson {...props} />;
    case "pdf":
      return <PdfLesson {...props} />;
    case "assignment":
      return <AssignmentLesson key={props.enrollmentId + props.lesson.id} {...props} />;
    case "quiz":
      // Published Phase 2 placeholders have no quiz definition.
      return typeof props.lesson.content.quiz_id === "string" ? (
        <QuizLesson
          key={props.enrollmentId + props.lesson.id}
          enrollmentId={props.enrollmentId}
          lessonId={props.lesson.id}
          completed={props.progress?.status === "completed"}
        />
      ) : (
        <PlaceholderLesson />
      );
    default:
      return <PlaceholderLesson />;
  }
}

function Completed() {
  return (
    <p role="status" className="text-sm font-medium text-primary">
      ✓ Completed
    </p>
  );
}

function MarkComplete({
  enrollmentId,
  lessonId,
  disabled,
  hint,
}: {
  enrollmentId: string;
  lessonId: string;
  disabled?: boolean;
  hint?: string;
}) {
  const complete = useCompleteLesson(enrollmentId);
  return (
    <div className="flex flex-col gap-1">
      <div>
        <Button
          onClick={() =>
            complete.mutate(lessonId, {
              onSuccess: (result) =>
                toast.success(
                  result.enrollment.completed_at
                    ? "Course completed!"
                    : `Lesson completed · ${result.enrollment.progress_percent}% of the course`,
                ),
              onError: (error) => toast.error(errorMessage(error)),
            })
          }
          disabled={disabled || complete.isPending}
        >
          {complete.isPending ? "Saving…" : "Mark complete"}
        </Button>
      </div>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------- video

function VideoLesson({ enrollmentId, lesson, progress }: Props) {
  const qc = useQueryClient();
  // Watching completes the lesson on the server (when buffered progress is flushed): keep the
  // ticks and progress bar current while the video is open.
  useQuery({ ...enrollmentQuery(enrollmentId), refetchInterval: 30_000 });
  const threshold = Math.round(Number(lesson.completion_threshold ?? "0.9") * 100);
  const watched = Math.round(Number(progress?.watched_ratio ?? 0) * 100);
  return (
    <div className="flex flex-col gap-3">
      <EnrollmentVideo
        enrollmentId={enrollmentId}
        lessonId={lesson.id}
        onVideoChanged={() => {
          toast.info("This video was updated. Loading the new version.");
          void qc.invalidateQueries({ queryKey: learnKeys.enrollment(enrollmentId) });
        }}
      />
      {progress?.status === "completed" ? (
        <Completed />
      ) : (
        <p className="text-sm text-muted-foreground">
          Completes when you have watched {threshold}% of the video
          {watched ? ` (${watched}% so far)` : ""}.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------- notes

function NotesLesson({ enrollmentId, lesson, progress }: Props) {
  const html = typeof lesson.content.html === "string" ? lesson.content.html : "";
  const imageIds = Array.isArray(lesson.content.image_file_ids)
    ? lesson.content.image_file_ids
    : [];
  const images = useQuery({
    ...notesImagesQuery(enrollmentId, lesson.id),
    enabled: imageIds.length > 0,
  });
  const container = useRef<HTMLDivElement>(null);
  const urls = images.data?.urls;

  // Images are `<img data-file-id>` in the published HTML; give them short-lived signed URLs.
  useEffect(() => {
    if (!container.current || !urls) return;
    for (const img of container.current.querySelectorAll<HTMLImageElement>("img[data-file-id]")) {
      const url = urls[img.dataset.fileId ?? ""];
      if (url) {
        img.src = url;
        img.loading = "lazy";
        img.decoding = "async";
      }
    }
  }, [urls, html]);

  return (
    <div className="flex flex-col gap-4">
      {html ? (
        <div
          ref={container}
          className="notes-content"
          // Rendered and sanitized by the API at publish time (allow-list + nh3).
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <p className="text-sm text-muted-foreground">This lesson has no notes yet.</p>
      )}
      {progress?.status === "completed" ? (
        <Completed />
      ) : lesson.is_required ? (
        <MarkComplete enrollmentId={enrollmentId} lessonId={lesson.id} />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------- pdf

function PdfLesson({ enrollmentId, lesson, progress }: Props) {
  const access = usePdfAccess(enrollmentId);
  const [fallback, setFallback] = useState<{ url: string; name: string } | null>(null);
  const opened = !!progress?.pdf_opened_at;

  async function open() {
    // Open the tab during the click (pop-up blockers allow it), then point it at the signed URL.
    const tab = window.open("about:blank", "_blank");
    try {
      const file = await access.mutateAsync(lesson.id);
      if (tab) {
        tab.opener = null;
        tab.location.href = file.url;
      } else {
        setFallback({ url: file.url, name: file.file_name });
      }
    } catch (error) {
      tab?.close();
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant={opened ? "outline" : "default"} onClick={() => void open()}>
          {access.isPending ? "Opening…" : opened ? "Open the PDF again" : "Open the PDF"}
        </Button>
        {fallback ? (
          <a href={fallback.url} target="_blank" rel="noopener noreferrer" className="underline">
            {fallback.name}
          </a>
        ) : null}
      </div>
      {progress?.status === "completed" ? (
        <Completed />
      ) : lesson.is_required ? (
        <MarkComplete
          enrollmentId={enrollmentId}
          lessonId={lesson.id}
          disabled={!opened}
          hint={opened ? undefined : "Open the PDF first, then mark it complete."}
        />
      ) : null}
    </div>
  );
}

function PlaceholderLesson() {
  return (
    <Alert>
      <AlertTitle>Coming soon</AlertTitle>
      <AlertDescription>
        This activity opens in a later release. It doesn&apos;t count toward your progress yet.
      </AlertDescription>
    </Alert>
  );
}
