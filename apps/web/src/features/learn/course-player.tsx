"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Circle, CircleDot } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { PageSkeleton } from "@/components/patterns/states";
import { ErrorAlert } from "@/features/admin/ui";
import { cn } from "@/lib/utils";

import { enrollmentQuery, useVisitLesson } from "./api";
import { LessonView } from "./lesson-views";
import {
  allLessons,
  isCompleted,
  neighbours,
  progressByLesson,
  readOutline,
  resumeLessonId,
  type LessonProgress,
  type Outline,
} from "./outline";

/** /learn/enrollments/[id]: go to where the student left off. */
export function ResumeCourse({ enrollmentId }: { enrollmentId: string }) {
  const router = useRouter();
  const detail = useQuery(enrollmentQuery(enrollmentId));
  const target = detail.data
    ? resumeLessonId(
        readOutline(detail.data.outline),
        progressByLesson(detail.data.progress),
        detail.data.enrollment.last_lesson_id,
      )
    : null;
  useEffect(() => {
    if (target) router.replace(`/learn/enrollments/${enrollmentId}/lessons/${target}`);
  }, [target, enrollmentId, router]);
  if (detail.error)
    return <ErrorAlert error={detail.error} onRetry={() => void detail.refetch()} />;
  if (detail.data && !target) return <p>This course has no lessons yet.</p>;
  return <PageSkeleton kind="form" rows={4} />;
}

export function CoursePlayer({
  enrollmentId,
  lessonId,
}: {
  enrollmentId: string;
  lessonId: string;
}) {
  const detail = useQuery(enrollmentQuery(enrollmentId));
  const visit = useVisitLesson(enrollmentId);
  const { mutate: recordVisit } = visit;
  useEffect(() => {
    recordVisit(lessonId);
  }, [lessonId, recordVisit]);

  if (detail.isPending) return <PageSkeleton kind="form" rows={4} />;
  if (detail.error)
    return <ErrorAlert error={detail.error} onRetry={() => void detail.refetch()} />;
  const outline = readOutline(detail.data.outline);
  const progress = progressByLesson(detail.data.progress);
  const lesson = allLessons(outline).find((l) => l.id === lessonId);
  const { enrollment } = detail.data;
  const { previous, next } = neighbours(outline, lessonId);

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-2">
        <Link href="/learn" className="text-sm text-muted-foreground hover:underline">
          ← My learning
        </Link>
        <h1>{outline.course.title || enrollment.course_title}</h1>
        <Progress value={enrollment.progress_percent} aria-label="Course progress" />
        <p className="text-xs text-muted-foreground tabular-nums" role="status">
          {enrollment.completed_at
            ? "Course completed"
            : `${enrollment.progress_percent}% complete`}
        </p>
      </header>

      <div className="flex flex-col gap-4 lg:flex-row-reverse lg:items-start">
        {/* Phones: a native drawer (no JS). Large screens: a sticky sidebar. */}
        <details className="rounded-lg border lg:hidden">
          <summary className="cursor-pointer px-3 py-2 text-sm font-medium">Course outline</summary>
          <OutlineNav
            outline={outline}
            progress={progress}
            enrollmentId={enrollmentId}
            current={lessonId}
          />
        </details>
        <aside className="hidden w-72 shrink-0 rounded-lg border lg:sticky lg:top-4 lg:block">
          <h2 className="border-b px-3 py-2 text-sm font-medium">Course outline</h2>
          <OutlineNav
            outline={outline}
            progress={progress}
            enrollmentId={enrollmentId}
            current={lessonId}
          />
        </aside>

        <article className="flex min-w-0 flex-1 flex-col gap-4" aria-label="Lesson">
          {lesson ? (
            <>
              <h2 className="text-base font-semibold sm:text-lg">{lesson.title}</h2>
              <LessonView
                key={lesson.id}
                enrollmentId={enrollmentId}
                lesson={lesson}
                progress={progress.get(lesson.id)}
              />
            </>
          ) : (
            <p role="alert">This lesson isn&apos;t in your version of the course.</p>
          )}
          <nav aria-label="Lessons" className="flex justify-between gap-2 border-t pt-3">
            {previous ? (
              <Link
                href={`/learn/enrollments/${enrollmentId}/lessons/${previous.id}`}
                className={buttonVariants({ variant: "outline" })}
              >
                ← Previous
              </Link>
            ) : (
              <span />
            )}
            {next ? (
              <Link
                href={`/learn/enrollments/${enrollmentId}/lessons/${next.id}`}
                className={buttonVariants()}
              >
                Next →
              </Link>
            ) : null}
          </nav>
        </article>
      </div>
    </div>
  );
}

function OutlineNav({
  outline,
  progress,
  enrollmentId,
  current,
}: {
  outline: Outline;
  progress: Map<string, LessonProgress>;
  enrollmentId: string;
  current: string;
}) {
  return (
    <ol className="flex flex-col gap-3 p-3" aria-label="Course outline">
      {outline.modules.map((mod) => (
        <li key={mod.id} className="flex flex-col gap-1">
          <span className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            {mod.title}
          </span>
          <ol className="flex flex-col">
            {mod.lessons.map((lesson) => {
              const done = isCompleted(progress, lesson.id);
              const started = progress.get(lesson.id)?.status === "in_progress";
              const Icon = done ? CheckCircle2 : started ? CircleDot : Circle;
              return (
                <li key={lesson.id}>
                  <Link
                    href={`/learn/enrollments/${enrollmentId}/lessons/${lesson.id}`}
                    aria-current={lesson.id === current ? "page" : undefined}
                    className={cn(
                      "flex min-h-10 items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted",
                      "aria-[current=page]:bg-muted aria-[current=page]:font-medium",
                    )}
                  >
                    <Icon
                      className={cn(
                        "size-4 shrink-0",
                        done ? "text-primary" : "text-muted-foreground",
                      )}
                      aria-hidden
                    />
                    <span className="min-w-0 flex-1 truncate">{lesson.title}</span>
                    <span className="sr-only">
                      {done ? "(completed)" : started ? "(started)" : ""}
                    </span>
                    {!lesson.is_required ? <Badge variant="secondary">Optional</Badge> : null}
                  </Link>
                </li>
              );
            })}
          </ol>
        </li>
      ))}
    </ol>
  );
}
