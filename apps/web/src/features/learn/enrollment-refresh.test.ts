import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";

import { myEnrollmentsQuery } from "./api";
import type { Enrollment } from "./outline";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));

const older: Enrollment = {
  id: "older",
  course_id: "old-course",
  course_title: "Existing course",
  status: "active",
  major_version: 1,
  version: "1.0",
  progress_percent: 0,
  enrolled_at: "2026-10-07T00:00:00Z",
  last_accessed_at: null,
  last_lesson_id: null,
  completed_at: null,
};

it("discovers a worker-committed enrollment after a nonempty paged initial read", async () => {
  vi.useFakeTimers();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const observer = new QueryObserver(client, myEnrollmentsQuery());
  const newer: Enrollment = { ...older, id: "newer", course_id: "new-course" };
  const response = (items: Enrollment[], next_cursor: string | null) => ({
    data: { items, next_cursor },
    response: new Response(null, { status: 200 }),
  });
  get
    .mockResolvedValueOnce(response([older], "older-page"))
    .mockResolvedValueOnce(response([], null))
    .mockResolvedValueOnce(response([newer, older], null));
  const unsubscribe = observer.subscribe(() => {});
  try {
    await vi.advanceTimersByTimeAsync(0);
    expect(observer.getCurrentResult().data).toEqual([older]);
    expect(get).toHaveBeenNthCalledWith(2, "/api/v1/enrollments", {
      params: { query: { limit: 100, cursor: "older-page" } },
    });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(observer.getCurrentResult().data).toEqual([newer, older]);
    // Each refresh begins at the newest page, rather than continuing the old cursor.
    expect(get).toHaveBeenNthCalledWith(3, "/api/v1/enrollments", {
      params: { query: { limit: 100, cursor: undefined } },
    });
    unsubscribe();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(get).toHaveBeenCalledTimes(3);
  } finally {
    unsubscribe();
    client.clear();
    vi.useRealTimers();
  }
});
