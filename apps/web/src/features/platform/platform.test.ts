import { describe, expect, it } from "vitest";

import { istDayEnd, istDayStart } from "./api";
import { organizationFormSchema, slugify } from "./organizations";
import { uuidParam } from "./params";

describe("slugify", () => {
  it.each([
    ["St. Xavier's College, Mumbai", "st-xavier-s-college-mumbai"],
    ["  IIT  (BHU) Varanasi ", "iit-bhu-varanasi"],
    ["Café Résumé", "cafe-resume"],
    ["---", ""],
  ])("%s -> %s", (name, slug) => {
    expect(slugify(name)).toBe(slug);
  });

  it("produces slugs the API accepts", () => {
    const parsed = organizationFormSchema.safeParse({
      name: "Shivaji College",
      slug: slugify("Shivaji College"),
      is_content_publisher: false,
    });
    expect(parsed.success).toBe(true);
    expect(
      organizationFormSchema.safeParse({ name: "X", slug: "Bad Slug", is_content_publisher: false })
        .success,
    ).toBe(false);
  });
});

describe("audit date range (India time)", () => {
  it("starts at midnight IST and ends at the next midnight", () => {
    expect(istDayStart("2026-10-02")).toBe("2026-10-01T18:30:00.000Z");
    expect(istDayEnd("2026-10-02")).toBe("2026-10-02T18:30:00.000Z");
  });
});

describe("uuidParam", () => {
  it("accepts one UUID and ignores anything else", () => {
    const id = "01a0fd1e-173a-7193-a02a-8849202dd985";
    expect(uuidParam(id)).toBe(id);
    expect(uuidParam([id, id])).toBeUndefined();
    expect(uuidParam("1 OR 1=1")).toBeUndefined();
    expect(uuidParam(undefined)).toBeUndefined();
  });
});
