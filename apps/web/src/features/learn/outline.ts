import type { components } from "@/lib/api/schema";

export type Enrollment = components["schemas"]["EnrollmentOut"];
export type EnrollmentDetail = components["schemas"]["EnrollmentDetail"];
export type LessonProgress = components["schemas"]["LessonProgressOut"];

export type LessonType = "video" | "notes" | "pdf" | "quiz" | "lab" | "assignment";

/** One lesson of a published version snapshot (what the API stores at publish). */
export type OutlineLesson = {
  id: string;
  title: string;
  lesson_type: LessonType;
  position: number;
  is_required: boolean;
  completion_threshold: string | null;
  estimated_minutes: number | null;
  video_duration_seconds: number | null;
  /** notes: {html, image_file_ids}; video: {video_asset_id}; pdf: {file_id};
   * assignment: {assignment_id, title, instructions_html, due_at, max_marks, submission_kinds} */
  content: Record<string, unknown>;
};
export type OutlineModule = {
  id: string;
  title: string;
  position: number;
  lessons: OutlineLesson[];
};
export type Outline = {
  course: { id: string; title: string; description: string };
  modules: OutlineModule[];
};

export const PLACEHOLDER_TYPES: ReadonlySet<LessonType> = new Set(["lab"]);

/** Read the snapshot defensively: it is typed as an open object in the API schema. */
export function readOutline(raw: unknown): Outline {
  const value = (raw ?? {}) as Partial<Outline>;
  return {
    course: value.course ?? { id: "", title: "", description: "" },
    modules: Array.isArray(value.modules) ? value.modules : [],
  };
}

export const allLessons = (outline: Outline): OutlineLesson[] =>
  outline.modules.flatMap((m) => m.lessons);

export const countsTowardProgress = (lesson: OutlineLesson): boolean =>
  lesson.is_required && !PLACEHOLDER_TYPES.has(lesson.lesson_type);

export function progressByLesson(progress: LessonProgress[]): Map<string, LessonProgress> {
  return new Map(progress.map((p) => [p.lesson_id, p]));
}

export const isCompleted = (progress: Map<string, LessonProgress>, lessonId: string): boolean =>
  progress.get(lessonId)?.status === "completed";

/**
 * Where "Continue" takes the student: the lesson they last opened (if it still exists in their
 * version), else the first unfinished lesson that counts, else the first lesson.
 */
export function resumeLessonId(
  outline: Outline,
  progress: Map<string, LessonProgress>,
  lastLessonId: string | null,
): string | null {
  const lessons = allLessons(outline);
  if (lastLessonId && lessons.some((l) => l.id === lastLessonId)) return lastLessonId;
  const next = lessons.find((l) => countsTowardProgress(l) && !isCompleted(progress, l.id));
  return (next ?? lessons[0])?.id ?? null;
}

export function neighbours(
  outline: Outline,
  lessonId: string,
): { previous: OutlineLesson | null; next: OutlineLesson | null } {
  const lessons = allLessons(outline);
  const index = lessons.findIndex((l) => l.id === lessonId);
  if (index === -1) return { previous: null, next: null };
  return { previous: lessons[index - 1] ?? null, next: lessons[index + 1] ?? null };
}

/**
 * Dashboard order. "Continue learning": unfinished courses already started, most recently
 * opened first. Then everything else: unfinished courses not started yet (newest enrollment
 * first), then completed ones.
 */
export function splitDashboard(enrollments: Enrollment[]): {
  continueLearning: Enrollment[];
  others: Enrollment[];
} {
  const time = (value: string | null) => (value ? Date.parse(value) : 0);
  const unfinished = enrollments.filter((e) => !e.completed_at);
  const continueLearning = unfinished
    .filter((e) => e.last_accessed_at)
    .sort((a, b) => time(b.last_accessed_at) - time(a.last_accessed_at));
  const notStarted = unfinished
    .filter((e) => !e.last_accessed_at)
    .sort((a, b) => time(b.enrolled_at) - time(a.enrolled_at));
  const completed = enrollments
    .filter((e) => e.completed_at)
    .sort((a, b) => time(b.completed_at) - time(a.completed_at));
  return { continueLearning, others: [...notStarted, ...completed] };
}
