import { describe, expect, it } from "vitest";

import type { DraftModule, LessonSummary } from "@/lib/api/types";

import { moveItem, moveLesson, moveModule, stepLesson } from "./reorder";

const lesson = (id: string, moduleId: string, position: number): LessonSummary => ({
  id,
  module_id: moduleId,
  position,
  title: id,
  lesson_type: "notes",
  is_required: true,
  completion_threshold: null,
  estimated_minutes: null,
  skill_ids: [],
});

function outline(): DraftModule[] {
  return [
    { id: "m1", title: "M1", position: 1, lessons: [lesson("a", "m1", 1), lesson("b", "m1", 2)] },
    { id: "m2", title: "M2", position: 2, lessons: [lesson("c", "m2", 1)] },
    { id: "m3", title: "M3", position: 3, lessons: [] },
  ];
}

const shape = (modules: DraftModule[]) =>
  modules.map((m) => `${m.id}:${m.lessons.map((l) => `${l.id}${l.position}`).join(",")}`);

describe("moveItem", () => {
  it("moves and clamps", () => {
    expect(moveItem([1, 2, 3], 0, 2)).toEqual([2, 3, 1]);
    expect(moveItem([1, 2, 3], 2, -5)).toEqual([3, 1, 2]);
    expect(moveItem([1, 2, 3], 9, 0)).toEqual([1, 2, 3]);
  });
});

describe("moveModule", () => {
  it("returns the new order with renumbered positions", () => {
    const result = moveModule(outline(), "m3", 0)!;
    expect(result.ids).toEqual(["m3", "m1", "m2"]);
    expect(result.modules.map((m) => m.position)).toEqual([1, 2, 3]);
  });

  it("is a no-op for the same place or an unknown module", () => {
    expect(moveModule(outline(), "m1", 0)).toBeNull();
    expect(moveModule(outline(), "nope", 1)).toBeNull();
  });
});

describe("moveLesson", () => {
  it("reorders within a module", () => {
    const result = moveLesson(outline(), "b", "m1", 0)!;
    expect(result).toMatchObject({ moduleId: "m1", ids: ["b", "a"] });
    expect(shape(result.modules)).toEqual(["m1:b1,a2", "m2:c1", "m3:"]);
  });

  it("moves between modules in one call, keeping the lesson id", () => {
    const result = moveLesson(outline(), "a", "m2", 1)!;
    expect(result).toMatchObject({ moduleId: "m2", ids: ["c", "a"] });
    expect(shape(result.modules)).toEqual(["m1:b1", "m2:c1,a2", "m3:"]);
    expect(result.modules[1]!.lessons[1]!.module_id).toBe("m2");
  });

  it("moves into an empty module", () => {
    const result = moveLesson(outline(), "c", "m3", 0)!;
    expect(result).toMatchObject({ moduleId: "m3", ids: ["c"] });
    expect(shape(result.modules)).toEqual(["m1:a1,b2", "m2:", "m3:c1"]);
  });

  it("does not mutate its input", () => {
    const before = outline();
    moveLesson(before, "a", "m2", 0);
    expect(shape(before)).toEqual(["m1:a1,b2", "m2:c1", "m3:"]);
  });

  it("is a no-op when nothing changes or ids are unknown", () => {
    expect(moveLesson(outline(), "a", "m1", 0)).toBeNull();
    expect(moveLesson(outline(), "zz", "m1", 0)).toBeNull();
    expect(moveLesson(outline(), "a", "zz", 0)).toBeNull();
  });
});

describe("stepLesson", () => {
  it("steps within and across module boundaries", () => {
    expect(stepLesson(outline(), "a", 1)).toEqual({ toModuleId: "m1", toIndex: 1 });
    expect(stepLesson(outline(), "b", 1)).toEqual({ toModuleId: "m2", toIndex: 0 });
    expect(stepLesson(outline(), "c", -1)).toEqual({ toModuleId: "m1", toIndex: 2 });
    expect(stepLesson(outline(), "c", 1)).toEqual({ toModuleId: "m3", toIndex: 0 });
    expect(stepLesson(outline(), "a", -1)).toBeNull();
  });
});
