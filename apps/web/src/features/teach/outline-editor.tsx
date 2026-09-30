"use client";

import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, GripVertical } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent, type ReactNode } from "react";

import { NativeSelect } from "@/components/native-select";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmButton, EmptyState, ErrorAlert } from "@/features/admin/ui";
import {
  LESSON_TYPE_LABELS,
  PLACEHOLDER_LESSON_TYPES,
  type DraftModule,
  type LessonSummary,
  type LessonType,
} from "@/lib/api/types";

import { draftQuery } from "./api";
import {
  useAddLesson,
  useAddModule,
  useDeleteLesson,
  useDeleteModule,
  useMoveLesson,
  useMoveModule,
  useRenameModule,
} from "./outline-hooks";
import { moveLesson, moveModule, stepLesson } from "./reorder";

const MODULE = "module:";
const LESSON = "lesson:";

export function OutlineEditor({ courseId }: { courseId: string }) {
  const draft = useQuery(draftQuery(courseId));
  const moveModuleEdit = useMoveModule(courseId);
  const moveLessonEdit = useMoveLesson(courseId);
  const [dragging, setDragging] = useState<string | null>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  if (draft.isPending) return <Skeleton className="h-48 w-full" />;
  if (draft.error) return <ErrorAlert error={draft.error} />;
  const modules = draft.data.modules;

  function requestModuleMove(moduleId: string, toIndex: number) {
    const result = moveModule(modules, moduleId, toIndex);
    if (result) moveModuleEdit.mutate({ moduleId, toIndex, ids: result.ids });
  }

  function requestLessonMove(lessonId: string, toModuleId: string, toIndex: number) {
    const result = moveLesson(modules, lessonId, toModuleId, toIndex);
    if (result) moveLessonEdit.mutate({ lessonId, toModuleId, toIndex, ids: result.ids });
  }

  function onDragEnd({ active, over }: DragEndEvent) {
    setDragging(null);
    if (!over) return;
    const activeId = String(active.id);
    const overId = String(over.id);
    if (activeId.startsWith(MODULE)) {
      const targetModule = overId.startsWith(MODULE)
        ? overId.slice(MODULE.length)
        : modules.find((m) => m.lessons.some((l) => `${LESSON}${l.id}` === overId))?.id;
      const toIndex = modules.findIndex((m) => m.id === targetModule);
      if (toIndex !== -1) requestModuleMove(activeId.slice(MODULE.length), toIndex);
      return;
    }
    const lessonId = activeId.slice(LESSON.length);
    if (overId.startsWith(MODULE)) {
      // Dropped on a module (e.g. an empty one): append to it.
      const target = modules.find((m) => `${MODULE}${m.id}` === overId);
      if (target) requestLessonMove(lessonId, target.id, target.lessons.length);
      return;
    }
    const target = modules.find((m) => m.lessons.some((l) => `${LESSON}${l.id}` === overId));
    if (!target) return;
    const toIndex = target.lessons.findIndex((l) => `${LESSON}${l.id}` === overId);
    requestLessonMove(lessonId, target.id, toIndex);
  }

  const dragged = dragging ? describeDragged(modules, dragging) : null;
  return (
    <section className="flex flex-col gap-4" aria-label="Outline">
      {modules.length === 0 ? <EmptyState>Add a module, then add lessons to it.</EmptyState> : null}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={({ active }: DragStartEvent) => setDragging(String(active.id))}
        onDragCancel={() => setDragging(null)}
        onDragEnd={onDragEnd}
      >
        <SortableContext
          items={modules.map((m) => `${MODULE}${m.id}`)}
          strategy={verticalListSortingStrategy}
        >
          <ol className="flex flex-col gap-3" aria-label="Modules">
            {modules.map((mod, index) => (
              <ModuleCard
                key={mod.id}
                courseId={courseId}
                mod={mod}
                index={index}
                count={modules.length}
                onMoveModule={requestModuleMove}
                onStepLesson={(lessonId, direction) => {
                  const step = stepLesson(modules, lessonId, direction);
                  if (step) requestLessonMove(lessonId, step.toModuleId, step.toIndex);
                }}
                isFirstLesson={(lessonId) => modules[0]?.lessons[0]?.id === lessonId}
                isLastLesson={(lessonId) => modules.at(-1)?.lessons.at(-1)?.id === lessonId}
              />
            ))}
          </ol>
        </SortableContext>
        <DragOverlay>
          {dragged ? (
            <div className="rounded-md border bg-background px-3 py-2 text-sm font-medium shadow-lg">
              {dragged}
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
      <AddModuleForm courseId={courseId} />
    </section>
  );
}

function describeDragged(modules: DraftModule[], id: string): string | null {
  if (id.startsWith(MODULE)) return modules.find((m) => `${MODULE}${m.id}` === id)?.title ?? null;
  for (const mod of modules) {
    const lesson = mod.lessons.find((l) => `${LESSON}${l.id}` === id);
    if (lesson) return lesson.title;
  }
  return null;
}

function DragHandle({
  label,
  listeners,
  attributes,
}: {
  label: string;
  listeners: ReturnType<typeof useSortable>["listeners"];
  attributes: ReturnType<typeof useSortable>["attributes"];
}) {
  return (
    <button
      type="button"
      className="touch-none rounded p-1 text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      aria-label={label}
      {...attributes}
      {...listeners}
    >
      <GripVertical className="size-4" aria-hidden />
    </button>
  );
}

function StepButtons({
  label,
  canUp,
  canDown,
  onStep,
}: {
  label: string;
  canUp: boolean;
  canDown: boolean;
  onStep: (direction: -1 | 1) => void;
}) {
  return (
    <span className="flex">
      <Button
        type="button"
        size="icon-sm"
        variant="ghost"
        disabled={!canUp}
        aria-label={`Move ${label} up`}
        onClick={() => onStep(-1)}
      >
        <ArrowUp aria-hidden />
      </Button>
      <Button
        type="button"
        size="icon-sm"
        variant="ghost"
        disabled={!canDown}
        aria-label={`Move ${label} down`}
        onClick={() => onStep(1)}
      >
        <ArrowDown aria-hidden />
      </Button>
    </span>
  );
}

function ModuleCard({
  courseId,
  mod,
  index,
  count,
  onMoveModule,
  onStepLesson,
  isFirstLesson,
  isLastLesson,
}: {
  courseId: string;
  mod: DraftModule;
  index: number;
  count: number;
  onMoveModule: (moduleId: string, toIndex: number) => void;
  onStepLesson: (lessonId: string, direction: -1 | 1) => void;
  isFirstLesson: (lessonId: string) => boolean;
  isLastLesson: (lessonId: string) => boolean;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: `${MODULE}${mod.id}`,
  });
  const [renaming, setRenaming] = useState(false);
  const deleteModule = useDeleteModule(courseId);
  const style = {
    transform: CSS.Transform.toString(transform),
    transition: transition,
  };
  return (
    <li
      ref={setNodeRef}
      style={style}
      className={`rounded-lg border bg-card ${isDragging ? "opacity-50" : ""}`}
      aria-label={`Module ${index + 1}: ${mod.title}`}
    >
      <div className="flex flex-wrap items-center gap-1 border-b p-2">
        <DragHandle
          label={`Drag module ${mod.title}`}
          listeners={listeners}
          attributes={attributes}
        />
        {renaming ? (
          <RenameModuleForm courseId={courseId} mod={mod} onDone={() => setRenaming(false)} />
        ) : (
          <h3 className="min-w-0 flex-1 truncate font-medium">
            {index + 1}. {mod.title}
          </h3>
        )}
        <span className="ml-auto flex items-center">
          <StepButtons
            label={`module ${mod.title}`}
            canUp={index > 0}
            canDown={index < count - 1}
            onStep={(direction) => onMoveModule(mod.id, index + direction)}
          />
          {!renaming ? (
            <Button size="sm" variant="ghost" onClick={() => setRenaming(true)}>
              Rename
            </Button>
          ) : null}
          <ConfirmButton
            label="Delete"
            variant="ghost"
            title={`Delete “${mod.title}”?`}
            description={
              mod.lessons.length
                ? `Its ${mod.lessons.length} lesson(s) are deleted too. Published versions keep them.`
                : "The module is removed from the draft."
            }
            confirmLabel="Delete module"
            onConfirm={() => deleteModule.mutateAsync(mod.id)}
          />
        </span>
      </div>
      <SortableContext
        items={mod.lessons.map((l) => `${LESSON}${l.id}`)}
        strategy={verticalListSortingStrategy}
      >
        <ol className="flex flex-col divide-y" aria-label={`Lessons in ${mod.title}`}>
          {mod.lessons.map((lesson) => (
            <LessonRow
              key={lesson.id}
              courseId={courseId}
              lesson={lesson}
              canUp={!isFirstLesson(lesson.id)}
              canDown={!isLastLesson(lesson.id)}
              onStep={(direction) => onStepLesson(lesson.id, direction)}
            />
          ))}
        </ol>
      </SortableContext>
      {mod.lessons.length === 0 ? (
        <p className="px-3 py-2 text-sm text-muted-foreground">No lessons yet.</p>
      ) : null}
      <AddLessonForm courseId={courseId} moduleId={mod.id} moduleTitle={mod.title} />
    </li>
  );
}

function LessonRow({
  courseId,
  lesson,
  canUp,
  canDown,
  onStep,
}: {
  courseId: string;
  lesson: LessonSummary;
  canUp: boolean;
  canDown: boolean;
  onStep: (direction: -1 | 1) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: `${LESSON}${lesson.id}`,
  });
  const deleteLesson = useDeleteLesson(courseId);
  const style = {
    transform: CSS.Transform.toString(transform),
    transition: transition,
  };
  const placeholder = PLACEHOLDER_LESSON_TYPES.has(lesson.lesson_type);
  return (
    <li
      ref={setNodeRef}
      style={style}
      className={`flex items-center gap-1 px-2 py-1.5 ${isDragging ? "opacity-50" : ""}`}
    >
      <DragHandle
        label={`Drag lesson ${lesson.title}`}
        listeners={listeners}
        attributes={attributes}
      />
      <Link
        href={`/teach/courses/${courseId}/lessons/${lesson.id}`}
        className="min-w-0 flex-1 truncate text-sm underline-offset-4 hover:underline"
      >
        {lesson.title}
      </Link>
      <Badge variant="outline">{LESSON_TYPE_LABELS[lesson.lesson_type]}</Badge>
      {placeholder ? (
        <Badge variant="secondary">Coming soon</Badge>
      ) : !lesson.is_required ? (
        <Badge variant="secondary">Optional</Badge>
      ) : null}
      <StepButtons
        label={`lesson ${lesson.title}`}
        canUp={canUp}
        canDown={canDown}
        onStep={onStep}
      />
      <ConfirmButton
        label="Delete"
        variant="ghost"
        title={`Delete “${lesson.title}”?`}
        description="It's removed from the draft. Published versions and student progress keep it."
        confirmLabel="Delete lesson"
        onConfirm={() => deleteLesson.mutateAsync(lesson.id)}
      />
    </li>
  );
}

function InlineForm({
  onSubmit,
  children,
  label,
}: {
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  children: ReactNode;
  label: string;
}) {
  return (
    <form
      onSubmit={onSubmit}
      className="flex flex-wrap items-end gap-2 p-2"
      aria-label={label}
      noValidate
    >
      {children}
    </form>
  );
}

const cleanTitle = (value: FormDataEntryValue | null) =>
  String(value ?? "")
    .trim()
    .slice(0, 200);

function RenameModuleForm({
  courseId,
  mod,
  onDone,
}: {
  courseId: string;
  mod: DraftModule;
  onDone: () => void;
}) {
  const rename = useRenameModule(courseId);
  return (
    <form
      className="flex min-w-0 flex-1 gap-2"
      aria-label={`Rename ${mod.title}`}
      onSubmit={(event) => {
        event.preventDefault();
        const title = cleanTitle(new FormData(event.currentTarget).get("title"));
        if (title && title !== mod.title) rename.mutate({ moduleId: mod.id, title });
        onDone();
      }}
    >
      <Input
        name="title"
        defaultValue={mod.title}
        aria-label="Module title"
        maxLength={200}
        autoFocus
      />
      <Button type="submit" size="sm">
        Save
      </Button>
    </form>
  );
}

function AddModuleForm({ courseId }: { courseId: string }) {
  const add = useAddModule(courseId);
  return (
    <InlineForm
      label="Add module"
      onSubmit={(event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const title = cleanTitle(new FormData(form).get("title"));
        if (!title) return;
        add.mutate(title, { onSuccess: () => form.reset() });
      }}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <Label htmlFor={`new-module-${courseId}`}>New module</Label>
        <Input
          id={`new-module-${courseId}`}
          name="title"
          maxLength={200}
          placeholder="e.g. Arrays"
        />
      </div>
      <Button type="submit" disabled={add.isPending}>
        Add module
      </Button>
    </InlineForm>
  );
}

const NEW_LESSON_TYPES: LessonType[] = ["video", "notes", "pdf", "quiz", "lab", "assignment"];

function AddLessonForm({
  courseId,
  moduleId,
  moduleTitle,
}: {
  courseId: string;
  moduleId: string;
  moduleTitle: string;
}) {
  const add = useAddLesson(courseId);
  const inputId = `new-lesson-${moduleId}`;
  return (
    <InlineForm
      label={`Add lesson to ${moduleTitle}`}
      onSubmit={(event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        const title = cleanTitle(data.get("title"));
        const lessonType = String(data.get("type")) as LessonType;
        if (!title || !NEW_LESSON_TYPES.includes(lessonType)) return;
        add.mutate({ moduleId, title, lessonType }, { onSuccess: () => form.reset() });
      }}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <Label htmlFor={inputId} className="text-xs text-muted-foreground">
          New lesson
        </Label>
        <Input id={inputId} name="title" maxLength={200} placeholder="Lesson title" />
      </div>
      <div className="flex w-32 flex-col gap-1.5">
        <Label htmlFor={`${inputId}-type`} className="text-xs text-muted-foreground">
          Type
        </Label>
        <NativeSelect id={`${inputId}-type`} name="type" defaultValue="video">
          {NEW_LESSON_TYPES.map((type) => (
            <option key={type} value={type}>
              {LESSON_TYPE_LABELS[type]}
            </option>
          ))}
        </NativeSelect>
      </div>
      <Button type="submit" variant="outline" disabled={add.isPending}>
        Add lesson
      </Button>
    </InlineForm>
  );
}
