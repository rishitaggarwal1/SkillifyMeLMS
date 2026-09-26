import { describe, expect, it } from "vitest";

import { seal, unseal } from "./crypto";
import { isAllowedOrigin, isUuid, needsRefresh, safeReturnTo, tokenExpiresAt } from "./policy";

const ORIGIN = "http://localhost:3000";
const headers = (h: Record<string, string>) => new Headers(h);

describe("cookie sealing", () => {
  it("round-trips and uses a fresh IV every time", async () => {
    const a = await seal("token-value", "secret-1");
    const b = await seal("token-value", "secret-1");
    expect(a).not.toEqual(b);
    expect(a).not.toContain("token-value");
    await expect(unseal(a, "secret-1")).resolves.toBe("token-value");
  });

  it("rejects tampering, the wrong key, and junk", async () => {
    const sealed = await seal("token-value", "secret-1");
    const [v, iv, data] = sealed.split(".");
    const flipped = `${v}.${iv}.${data!.slice(0, -2)}${data!.slice(-2) === "AA" ? "AB" : "AA"}`;
    await expect(unseal(flipped, "secret-1")).resolves.toBeNull();
    await expect(unseal(sealed, "secret-2")).resolves.toBeNull();
    await expect(unseal("garbage", "secret-1")).resolves.toBeNull();
    await expect(unseal(undefined, "secret-1")).resolves.toBeNull();
  });
});

describe("isAllowedOrigin (CSRF)", () => {
  it("allows safe methods without an Origin", () => {
    expect(isAllowedOrigin("GET", headers({}), ORIGIN)).toBe(true);
  });

  it("requires our own Origin on unsafe methods", () => {
    expect(isAllowedOrigin("POST", headers({ origin: ORIGIN }), ORIGIN)).toBe(true);
    expect(isAllowedOrigin("POST", headers({ origin: "https://evil.test" }), ORIGIN)).toBe(false);
    expect(isAllowedOrigin("DELETE", headers({}), ORIGIN)).toBe(false);
  });

  it("rejects cross-site fetch metadata even with a matching Origin", () => {
    expect(
      isAllowedOrigin("PATCH", headers({ origin: ORIGIN, "sec-fetch-site": "cross-site" }), ORIGIN),
    ).toBe(false);
    expect(
      isAllowedOrigin(
        "PATCH",
        headers({ origin: ORIGIN, "sec-fetch-site": "same-origin" }),
        ORIGIN,
      ),
    ).toBe(true);
  });
});

describe("safeReturnTo", () => {
  it.each([
    ["/admin/batches?tab=1#x", "/admin/batches?tab=1#x"],
    ["/", "/"],
    ["//evil.test/path", "/"],
    ["https://evil.test", "/"],
    ["/\\evil.test", "/"],
    ["javascript:alert(1)", "/"],
    [null, "/"],
  ])("%s -> %s", (input, expected) => {
    expect(safeReturnTo(input)).toBe(expected);
  });
});

describe("token expiry", () => {
  const jwt = (claims: object) => `h.${btoa(JSON.stringify(claims)).replace(/=+$/, "")}.s`;

  it("reads exp without verifying", () => {
    expect(tokenExpiresAt(jwt({ exp: 123 }))).toBe(123);
    expect(tokenExpiresAt("nope")).toBeNull();
  });

  it("refreshes shortly before expiry", () => {
    expect(needsRefresh(jwt({ exp: 1000 }), 900)).toBe(false);
    expect(needsRefresh(jwt({ exp: 1000 }), 980)).toBe(true);
    expect(needsRefresh(null, 0)).toBe(true);
    expect(needsRefresh("broken", 0)).toBe(true);
  });
});

it("isUuid", () => {
  expect(isUuid("01a0dc95-c45f-7bd0-8536-86b7025b06be")).toBe(true);
  expect(isUuid("not-a-uuid")).toBe(false);
});
