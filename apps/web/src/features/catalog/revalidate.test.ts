import { describe, expect, it } from "vitest";

import { decideRevalidation, secretMatches } from "./revalidate";

const SECRET = "a-long-random-revalidation-secret-for-tests";

describe("decideRevalidation", () => {
  it("revalidates allow-listed tags with the right secret", () => {
    expect(decideRevalidation(SECRET, SECRET, { tags: ["catalog", "catalog"] })).toEqual({
      ok: true,
      tags: ["catalog"],
    });
  });

  it("is disabled when no secret is configured", () => {
    expect(decideRevalidation(undefined, SECRET, { tags: ["catalog"] })).toMatchObject({
      ok: false,
      status: 503,
    });
    expect(decideRevalidation("", "", { tags: ["catalog"] })).toMatchObject({ status: 503 });
  });

  it.each([null, "", "wrong", `${SECRET}x`, SECRET.slice(0, -1)])(
    "rejects secret %j",
    (provided) => {
      expect(decideRevalidation(SECRET, provided, { tags: ["catalog"] })).toMatchObject({
        ok: false,
        status: 401,
      });
    },
  );

  it.each([null, {}, { tags: [] }, { tags: ["users"] }, { tags: "catalog" }, { tags: [1] }])(
    "rejects body %j",
    (body) => {
      expect(decideRevalidation(SECRET, SECRET, body)).toMatchObject({ ok: false, status: 400 });
    },
  );

  it("checks the secret before looking at the body", () => {
    expect(decideRevalidation(SECRET, "wrong", { tags: ["users"] })).toMatchObject({
      status: 401,
    });
  });
});

describe("secretMatches", () => {
  it("compares whole values", () => {
    expect(secretMatches(SECRET, SECRET)).toBe(true);
    expect(secretMatches("a", "ab")).toBe(false);
    expect(secretMatches(null, SECRET)).toBe(false);
  });
});
