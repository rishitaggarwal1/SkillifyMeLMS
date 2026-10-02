import { describe, expect, it } from "vitest";

import {
  neighbours,
  progressByLesson,
  readOutline,
  resumeLessonId,
  splitDashboard,
  type Enrollment,
  type LessonProgress,
  type OutlineLesson,
} from "./outline";

const lesson = (id: string, type: OutlineLesson["lesson_type"] = "notes", required = true) =>
  ({
    id,
    title: id,
    lesson_type: type,
    position: 1,
    is_required: required,
    completion_threshold: null,
    estimated_minutes: null,
    video_duration_seconds: null,
    content: {},
  }) satisfies OutlineLesson;

const outline = readOutline({
  course: { id: "c", title: "C", description: "" },
  modules: [
    { id: "m1", title: "M1", position: 1, lessons: [lesson("a"), lesson("q", "quiz", false)] },
    { id: "m2", title: "M2", position: 2, lessons: [lesson("b", "video"), lesson("c", "pdf")] },
  ],
});

const done = (id: string): LessonProgress => ({
  lesson_id: id,
  status: "completed",
  completed_at: "2026-10-01T10:00:00Z",
  pdf_opened_at: null,
  video_position_seconds: null,
  watched_ratio: null,
});

describe("resumeLessonId", () => {
  it("returns to the last lesson opened", () => {
    expect(resumeLessonId(outline, new Map(), "c")).toBe("c");
  });

  it("otherwise picks the first unfinished lesson that counts (skipping placeholders)", () => {
    const progress = progressByLesson([done("a")]);
    expect(resumeLessonId(outline, progress, null)).toBe("b");
    // A last lesson no longer in this version is ignored.
    expect(resumeLessonId(outline, progress, "gone")).toBe("b");
  });

  it("falls back to the first lesson when everything is done", () => {
    const progress = progressByLesson([done("a"), done("b"), done("c")]);
    expect(resumeLessonId(outline, progress, null)).toBe("a");
    expect(resumeLessonId(readOutline({}), progress, null)).toBeNull();
  });
});

describe("neighbours", () => {
  it("crosses module boundaries", () => {
    expect(neighbours(outline, "q")).toMatchObject({ previous: { id: "a" }, next: { id: "b" } });
    expect(neighbours(outline, "a").previous).toBeNull();
    expect(neighbours(outline, "c").next).toBeNull();
    expect(neighbours(outline, "zz")).toEqual({ previous: null, next: null });
  });
});

describe("readOutline", () => {
  it("tolerates a missing or partial snapshot", () => {
    expect(readOutline(null)).toEqual({
      course: { id: "", title: "", description: "" },
      modules: [],
    });
    expect(readOutline({ modules: "nope" }).modules).toEqual([]);
  });
});

describe("splitDashboard", () => {
  const enrollment = (id: string, over: Partial<Enrollment>): Enrollment => ({
    id,
    course_id: id,
    course_title: id,
    status: "active",
    major_version: 1,
    version: "1.0",
    progress_percent: 0,
    enrolled_at: "2026-09-01T00:00:00Z",
    last_accessed_at: null,
    last_lesson_id: null,
    completed_at: null,
    ...over,
  });

  it("puts started, unfinished courses first, most recent first", () => {
    const { continueLearning, others } = splitDashboard([
      enrollment("old", { last_accessed_at: "2026-09-20T00:00:00Z", progress_percent: 40 }),
      enrollment("done", {
        last_accessed_at: "2026-09-30T00:00:00Z",
        completed_at: "2026-09-30T00:00:00Z",
      }),
      enrollment("new", { enrolled_at: "2026-09-25T00:00:00Z" }),
      enrollment("recent", { last_accessed_at: "2026-09-29T00:00:00Z", progress_percent: 10 }),
      enrollment("older-new", { enrolled_at: "2026-09-02T00:00:00Z" }),
    ]);
    expect(continueLearning.map((e) => e.id)).toEqual(["recent", "old"]);
    expect(others.map((e) => e.id)).toEqual(["new", "older-new", "done"]);
  });
});
