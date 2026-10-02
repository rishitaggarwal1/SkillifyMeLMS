import { describe, expect, it } from "vitest";

import { formatIst, istInputToIso, isoToIstInput } from "./ist";

describe("IST dates", () => {
  it("reads a typed IST time as the right instant, and back", () => {
    expect(istInputToIso("2026-11-01T23:59")).toBe("2026-11-01T18:29:00.000Z");
    expect(isoToIstInput("2026-11-01T18:29:00.000Z")).toBe("2026-11-01T23:59");
    expect(isoToIstInput(istInputToIso("2026-02-28T00:30"))).toBe("2026-02-28T00:30");
  });

  it("treats empty and broken input as no date", () => {
    expect(istInputToIso("")).toBeNull();
    expect(istInputToIso("not a date")).toBeNull();
    expect(isoToIstInput(null)).toBe("");
  });

  it("formats in India time", () => {
    expect(formatIst("2026-11-01T18:29:00Z")).toMatch(/1 Nov 2026.*11:59.*pm IST/i);
  });
});
