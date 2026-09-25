import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { jsonResponse, mockApiClient, renderWithQuery } from "@/test/utils";

import { HealthStatus } from "./health-status";

describe("<HealthStatus />", () => {
  it("shows operational status and each dependency", async () => {
    const client = mockApiClient({
      "/health/ready": () =>
        jsonResponse({
          status: "ok",
          checks: {
            database: { status: "ok", latency_ms: 3.4, error: null },
            redis: { status: "ok", latency_ms: 0.9, error: null },
          },
        }),
    });

    renderWithQuery(<HealthStatus client={client} />);

    expect(await screen.findByText("All systems operational")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("check-database")).getByText("PostgreSQL"),
    ).toBeInTheDocument();
    expect(within(screen.getByTestId("check-redis")).getByText("OK")).toBeInTheDocument();
  });

  it("shows degraded status with the failing check", async () => {
    const client = mockApiClient({
      "/health/ready": () =>
        jsonResponse(
          {
            status: "unavailable",
            checks: {
              database: { status: "ok", latency_ms: 3.4, error: null },
              redis: { status: "error", latency_ms: 2000, error: "TimeoutError" },
            },
          },
          503,
        ),
    });

    renderWithQuery(<HealthStatus client={client} />);

    expect(await screen.findByText("Degraded")).toBeInTheDocument();
    expect(within(screen.getByTestId("check-redis")).getByText("TimeoutError")).toBeInTheDocument();
  });

  it("shows an alert when the API is unreachable", async () => {
    const client = mockApiClient({});

    renderWithQuery(<HealthStatus client={client} />);

    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
