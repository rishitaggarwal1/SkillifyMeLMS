import { describe, expect, it, vi } from "vitest";

import { catalogFetchOptions } from "./api";

vi.mock("server-only", () => ({}));

describe("catalogFetchOptions", () => {
  it("reads fresh only when CATALOG_DATA_CACHE=off", () => {
    expect(catalogFetchOptions({ CATALOG_DATA_CACHE: "off" })).toEqual({ cache: "no-store" });
  });

  it.each([{}, { CATALOG_DATA_CACHE: "on" }, { NODE_ENV: "development" }])(
    "uses the tagged data cache otherwise (%j)",
    (env) => {
      expect(catalogFetchOptions(env)).toEqual({ next: { tags: ["catalog"], revalidate: 300 } });
    },
  );
});
