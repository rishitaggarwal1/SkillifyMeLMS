import { queryOptions } from "@tanstack/react-query";

import { api, type ApiClient } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";

export type Readiness = components["schemas"]["ReadinessResponse"];

/**
 * GET /health/ready. A 503 still carries a readiness report (which dependency is down), so it
 * resolves with that report instead of throwing; only transport/unexpected failures throw.
 */
export async function fetchReadiness(client: ApiClient = api): Promise<Readiness> {
  const { data, error, response } = await client.GET("/health/ready");
  if (data) return data;
  if (response.status === 503 && error) return error;
  throw toApiError(response, error);
}

export const readinessQuery = (client: ApiClient = api) =>
  queryOptions({
    queryKey: ["health", "ready"] as const,
    queryFn: () => fetchReadiness(client),
    refetchInterval: 15_000,
    staleTime: 5_000,
  });
