import { useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import type { Draft, Lesson, LessonType } from "@/lib/api/types";
import { unwrap } from "@/lib/api/unwrap";

import { keys, useOutlineEdit, type IfMatch } from "./api";
import { moveLesson, moveModule } from "./reorder";

const cid = (courseId: string) => ({ course_id: courseId });

export function useAddModule(courseId: string) {
  return useOutlineEdit(
    courseId,
    (title: string, header: IfMatch) =>
      unwrap(
        api.POST("/api/v1/courses/{course_id}/modules", {
          params: { path: cid(courseId), header },
          body: { title },
        }),
      ),
    { revisionOf: (m) => m.course_revision },
  );
}

export function useRenameModule(courseId: string) {
  return useOutlineEdit(
    courseId,
    ({ moduleId, title }: { moduleId: string; title: string }, header: IfMatch) =>
      unwrap(
        api.PATCH("/api/v1/courses/{course_id}/modules/{module_id}", {
          params: { path: { course_id: courseId, module_id: moduleId }, header },
          body: { title },
        }),
      ),
    {
      revisionOf: (m) => m.course_revision,
      optimistic: (draft, { moduleId, title }) => ({
        ...draft,
        modules: draft.modules.map((m) => (m.id === moduleId ? { ...m, title } : m)),
      }),
    },
  );
}

export function useDeleteModule(courseId: string) {
  return useOutlineEdit(
    courseId,
    (moduleId: string, header: IfMatch) =>
      unwrap(
        api.DELETE("/api/v1/courses/{course_id}/modules/{module_id}", {
          params: { path: { course_id: courseId, module_id: moduleId }, header },
        }),
      ),
    {
      revisionOf: (r) => r.course_revision,
      optimistic: (draft, moduleId) => ({
        ...draft,
        modules: draft.modules.filter((m) => m.id !== moduleId),
      }),
    },
  );
}

export function useMoveModule(courseId: string) {
  return useOutlineEdit(
    courseId,
    ({ ids }: { moduleId: string; toIndex: number; ids: string[] }, header: IfMatch) =>
      unwrap(
        api.PUT("/api/v1/courses/{course_id}/modules/order", {
          params: { path: cid(courseId), header },
          body: { ids },
        }),
      ),
    {
      revisionOf: (draft) => draft,
      optimistic: (draft, { moduleId, toIndex }) => ({
        ...draft,
        modules: moveModule(draft.modules, moduleId, toIndex)?.modules ?? draft.modules,
      }),
    },
  );
}

export function useMoveLesson(courseId: string) {
  return useOutlineEdit(
    courseId,
    (
      { toModuleId, ids }: { lessonId: string; toModuleId: string; toIndex: number; ids: string[] },
      header: IfMatch,
    ) =>
      unwrap(
        api.PUT("/api/v1/courses/{course_id}/modules/{module_id}/lessons/order", {
          params: { path: { course_id: courseId, module_id: toModuleId }, header },
          body: { ids },
        }),
      ),
    {
      revisionOf: (draft) => draft,
      optimistic: (draft, { lessonId, toModuleId, toIndex }) => ({
        ...draft,
        modules: moveLesson(draft.modules, lessonId, toModuleId, toIndex)?.modules ?? draft.modules,
      }),
    },
  );
}

export function useAddLesson(courseId: string) {
  return useOutlineEdit(
    courseId,
    (
      { moduleId, title, lessonType }: { moduleId: string; title: string; lessonType: LessonType },
      header: IfMatch,
    ) =>
      unwrap(
        api.POST("/api/v1/courses/{course_id}/modules/{module_id}/lessons", {
          params: { path: { course_id: courseId, module_id: moduleId }, header },
          body: { title, lesson_type: lessonType, is_required: true },
        }),
      ),
    { revisionOf: (lesson) => lesson.course_revision },
  );
}

export function useDeleteLesson(courseId: string) {
  return useOutlineEdit(
    courseId,
    (lessonId: string, header: IfMatch) =>
      unwrap(
        api.DELETE("/api/v1/courses/{course_id}/lessons/{lesson_id}", {
          params: { path: { course_id: courseId, lesson_id: lessonId }, header },
        }),
      ),
    {
      revisionOf: (r) => r.course_revision,
      optimistic: (draft: Draft, lessonId) => ({
        ...draft,
        modules: draft.modules.map((m) => ({
          ...m,
          lessons: m.lessons.filter((l) => l.id !== lessonId),
        })),
      }),
    },
  );
}

export type LessonChanges = {
  title?: string;
  is_required?: boolean;
  estimated_minutes?: number | null;
  completion_threshold?: string | null;
  content?: Record<string, unknown>;
};

/** Save lesson fields or content; the lesson cache is updated from the response. */
export function useUpdateLesson(courseId: string, lessonId: string) {
  const qc = useQueryClient();
  return useOutlineEdit(
    courseId,
    (body: LessonChanges, header: IfMatch) =>
      unwrap(
        api.PATCH("/api/v1/courses/{course_id}/lessons/{lesson_id}", {
          params: { path: { course_id: courseId, lesson_id: lessonId }, header },
          body,
        }),
      ),
    {
      revisionOf: (lesson) => lesson.course_revision,
      onSuccess: (lesson: Lesson) => qc.setQueryData(keys.lesson(courseId, lessonId), lesson),
    },
  );
}

export function useSetLessonSkills(courseId: string, lessonId: string) {
  const qc = useQueryClient();
  return useOutlineEdit(
    courseId,
    (skillIds: string[], header: IfMatch) =>
      unwrap(
        api.PUT("/api/v1/courses/{course_id}/lessons/{lesson_id}/skills", {
          params: { path: { course_id: courseId, lesson_id: lessonId }, header },
          body: { skill_ids: skillIds },
        }),
      ),
    {
      revisionOf: (lesson) => lesson.course_revision,
      onSuccess: (lesson: Lesson) => qc.setQueryData(keys.lesson(courseId, lessonId), lesson),
    },
  );
}
