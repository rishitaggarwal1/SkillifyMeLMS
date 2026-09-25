import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/errors";
import { jsonResponse, mockApiClient } from "@/test/utils";

import { fetchReadiness } from "./queries";

const healthy = {
  status: "ok",
  checks: {
    database: { status: "ok", latency_ms: 1.2, error: null },
    redis: { status: "ok", latency_ms: 0.4, error: null },
  },
};

const degraded = {
  status: "unavailable",
  checks: {
    database: { status: "ok", latency_ms: 1.2, error: null },
    redis: { status: "error", latency_ms: 1000, error: "TimeoutError" },
  },
};

describe("fetchReadiness", () => {
  it("returns the report when healthy", async () => {
    const client = mockApiClient({ "/health/ready": () => jsonResponse(healthy) });

    await expect(fetchReadiness(client)).resolves.toEqual(healthy);
  });

  it("returns the report (not an error) on 503", async () => {
    const client = mockApiClient({ "/health/ready": () => jsonResponse(degraded, 503) });

    await expect(fetchReadiness(client)).resolves.toEqual(degraded);
  });

  it("throws an ApiError parsed from the error envelope", async () => {
    const client = mockApiClient({
      "/health/ready": () =>
        jsonResponse({ error: { code: "internal_error", message: "Boom", details: null } }, 500, {
          "x-request-id": "req-1",
        }),
    });

    const error = await fetchReadiness(client).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 500,
      code: "internal_error",
      message: "Boom",
      requestId: "req-1",
    });
  });

  it("throws a generic ApiError when the body is not an envelope", async () => {
    const client = mockApiClient({ "/health/ready": () => jsonResponse({ nope: true }, 502) });

    await expect(fetchReadiness(client)).rejects.toMatchObject({
      status: 502,
      code: "unexpected_response",
    });
  });
});
