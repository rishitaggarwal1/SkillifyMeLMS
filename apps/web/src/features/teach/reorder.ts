import type { DraftModule } from "@/lib/api/types";

/** `items` with the element at `from` moved to `to` (indices clamped). */
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  const next = [...items];
  if (from < 0 || from >= next.length) return next;
  const [item] = next.splice(from, 1) as [T];
  next.splice(Math.max(0, Math.min(to, next.length)), 0, item);
  return next;
}

function renumber(modules: DraftModule[]): DraftModule[] {
  return modules.map((mod, index) => ({
    ...mod,
    position: index + 1,
    lessons: mod.lessons.map((lesson, i) => ({
      ...lesson,
      module_id: mod.id,
      position: i + 1,
    })),
  }));
}

/** Move a module to `toIndex`. Returns the new outline and the full module order for the API
 * (`PUT /courses/{id}/modules/order`), or null when nothing changes. */
export function moveModule(
  modules: readonly DraftModule[],
  moduleId: string,
  toIndex: number,
): { modules: DraftModule[]; ids: string[] } | null {
  const from = modules.findIndex((m) => m.id === moduleId);
  if (from === -1) return null;
  const to = Math.max(0, Math.min(toIndex, modules.length - 1));
  if (from === to) return null;
  const next = renumber(moveItem(modules, from, to));
  return { modules: next, ids: next.map((m) => m.id) };
}

/** Move a lesson to `toIndex` within `toModuleId` (its own module or another one). Returns the new
 * outline and the target module's full lesson order: one `PUT .../modules/{id}/lessons/order`
 * call moves it, keeping its id. Null when nothing changes or the ids are unknown. */
export function moveLesson(
  modules: readonly DraftModule[],
  lessonId: string,
  toModuleId: string,
  toIndex: number,
): { modules: DraftModule[]; moduleId: string; ids: string[] } | null {
  const source = modules.find((m) => m.lessons.some((l) => l.id === lessonId));
  const target = modules.find((m) => m.id === toModuleId);
  if (!source || !target) return null;
  const lesson = source.lessons.find((l) => l.id === lessonId)!;
  const from = source.lessons.indexOf(lesson);
  const next = modules.map((m) => ({ ...m, lessons: [...m.lessons] }));
  const src = next.find((m) => m.id === source.id)!;
  const dst = next.find((m) => m.id === target.id)!;
  src.lessons.splice(from, 1);
  const to = Math.max(0, Math.min(toIndex, dst.lessons.length));
  if (src === dst && from === to) return null;
  dst.lessons.splice(to, 0, lesson);
  const renumbered = renumber(next);
  const moved = renumbered.find((m) => m.id === target.id)!;
  return { modules: renumbered, moduleId: target.id, ids: moved.lessons.map((l) => l.id) };
}

/** Where a lesson ends up when moved one step up or down: across a module boundary it joins the
 * end of the previous module (up) or the start of the next one (down). */
export function stepLesson(
  modules: readonly DraftModule[],
  lessonId: string,
  direction: -1 | 1,
): { toModuleId: string; toIndex: number } | null {
  const mi = modules.findIndex((m) => m.lessons.some((l) => l.id === lessonId));
  if (mi === -1) return null;
  const current = modules[mi]!;
  const li = current.lessons.findIndex((l) => l.id === lessonId);
  const within = li + direction;
  if (within >= 0 && within < current.lessons.length) {
    return { toModuleId: current.id, toIndex: within };
  }
  const neighbour = modules[mi + direction];
  if (!neighbour) return null;
  return { toModuleId: neighbour.id, toIndex: direction === -1 ? neighbour.lessons.length : 0 };
}
