// @vitest-environment node
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { config, proxy } from "./proxy";

describe.each(["admin", "learn", "platform", "teach"])("%s route protection", (area) => {
  const url = `https://portal.example.test/${area}/courses`;

  it("still sends a signed-out page navigation through login", () => {
    expect(unstable_doesMiddlewareMatch({ config, nextConfig: {}, url })).toBe(true);
    const response = proxy(new NextRequest(url));
    const destination = new URL(response.headers.get("location")!);
    expect(destination.pathname).toBe("/auth/login");
    expect(destination.searchParams.get("returnTo")).toBe(`/${area}/courses`);
  });

  it.each([{ "next-router-prefetch": "1" }, { purpose: "prefetch" }])(
    "does not start an extra login flow for a background prefetch (%j)",
    (headers) => {
      expect(unstable_doesMiddlewareMatch({ config, nextConfig: {}, url, headers })).toBe(false);
    },
  );
});
