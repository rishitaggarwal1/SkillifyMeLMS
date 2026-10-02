import { describe, expect, it, vi } from "vitest";

import { allPages } from "./all-pages";

describe("allPages", () => {
  it("follows the cursor to the last page", async () => {
    const fetchPage = vi.fn(async (cursor: string | undefined) =>
      cursor === undefined
        ? { items: [1, 2], next_cursor: "a" }
        : cursor === "a"
          ? { items: [3], next_cursor: "b" }
          : { items: [4], next_cursor: null },
    );
    await expect(allPages(fetchPage)).resolves.toEqual({ items: [1, 2, 3, 4], truncated: false });
    expect(fetchPage.mock.calls.map(([c]) => c)).toEqual([undefined, "a", "b"]);
  });

  it("stops at the page cap and reports it", async () => {
    const fetchPage = vi.fn(async () => ({ items: ["x"], next_cursor: "more" }));
    await expect(allPages(fetchPage, 3)).resolves.toEqual({
      items: ["x", "x", "x"],
      truncated: true,
    });
    expect(fetchPage).toHaveBeenCalledTimes(3);
  });
});
