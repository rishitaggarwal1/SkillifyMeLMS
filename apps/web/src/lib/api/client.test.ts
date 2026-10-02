// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

describe("api client on the server", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("imports without API_INTERNAL_URL (next build) and fails the first request clearly", async () => {
    vi.stubEnv("API_INTERNAL_URL", "");
    const { api } = await import("./client");
    await expect(api.GET("/api/v1/me")).rejects.toThrow("API_INTERNAL_URL is not set");
  });

  it("requires API_INTERNAL_URL for clients created per request", async () => {
    vi.stubEnv("API_INTERNAL_URL", "");
    const { apiBaseUrl } = await import("./client");
    expect(() => apiBaseUrl()).toThrow("API_INTERNAL_URL is not set");
    vi.stubEnv("API_INTERNAL_URL", "http://api.test");
    expect(apiBaseUrl()).toBe("http://api.test");
  });
});
