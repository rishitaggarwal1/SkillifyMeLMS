import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { toast } from "@/lib/toast";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import type { Draft } from "@/lib/api/types";

import { keys, useOutlineEdit, type IfMatch } from "./api";

vi.mock("@/lib/toast", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const COURSE = "c1";

function draft(revision: number, titles: string[]): Draft {
  return {
    course: { id: COURSE, revision } as Draft["course"],
    modules: titles.map((title, i) => ({ id: `m${i}`, title, position: i + 1, lessons: [] })),
  };
}

function setup(initial: Draft) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  });
  qc.setQueryData(keys.draft(COURSE), initial);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper, current: () => qc.getQueryData<Draft>(keys.draft(COURSE))! };
}

const rename = (d: Draft, title: string): Draft => ({
  ...d,
  modules: d.modules.map((m, i) => (i === 0 ? { ...m, title } : m)),
});

describe("useOutlineEdit", () => {
  it("sends the cached revision and adopts the one the API returns", async () => {
    const { wrapper, current } = setup(draft(7, ["Arrays"]));
    const run = vi.fn(async (_title: string, _h: IfMatch) => ({ course_revision: 8 }));
    const { result } = renderHook(
      () =>
        useOutlineEdit(COURSE, run, {
          revisionOf: (r) => r.course_revision,
          optimistic: rename,
        }),
      { wrapper },
    );
    await act(() => result.current.mutateAsync("Strings"));
    expect(run).toHaveBeenCalledWith("Strings", { "If-Match": "7" });
    expect(current().course.revision).toBe(8);
    expect(current().modules[0]!.title).toBe("Strings");
  });

  it("runs quick edits one at a time, each with the previous edit's revision", async () => {
    const { wrapper } = setup(draft(1, ["A"]));
    const seen: string[] = [];
    let revision = 1;
    const run = vi.fn(async (_title: string, header: IfMatch) => {
      seen.push(header["If-Match"]);
      await new Promise((resolve) => setTimeout(resolve, 5));
      return { course_revision: ++revision };
    });
    const { result } = renderHook(
      () => useOutlineEdit(COURSE, run, { revisionOf: (r) => r.course_revision }),
      { wrapper },
    );
    await act(() =>
      Promise.all([
        result.current.mutateAsync("B"),
        result.current.mutateAsync("C"),
        result.current.mutateAsync("D"),
      ]),
    );
    expect(seen).toEqual(["1", "2", "3"]);
  });

  it("rolls back an optimistic change on 409 and says why", async () => {
    const { wrapper, current } = setup(draft(3, ["Arrays"]));
    const conflict = new ApiError({ status: 409, code: "revision_conflict", message: "stale" });
    const run = vi.fn(async (_title: string, _h: IfMatch): Promise<{ course_revision: number }> => {
      expect(current().modules[0]!.title).toBe("Strings"); // optimistic while in flight
      throw conflict;
    });
    const { result } = renderHook(
      () =>
        useOutlineEdit(COURSE, run, { revisionOf: (r) => r.course_revision, optimistic: rename }),
      { wrapper },
    );
    await act(async () => {
      await expect(result.current.mutateAsync("Strings")).rejects.toBe(conflict);
    });
    await waitFor(() => expect(current().modules[0]!.title).toBe("Arrays"));
    expect(current().course.revision).toBe(3);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("changed elsewhere"));
  });
});
