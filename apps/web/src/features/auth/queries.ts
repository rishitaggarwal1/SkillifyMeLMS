import { queryOptions, useQuery } from "@tanstack/react-query";

import { api, type ApiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { Me } from "@/lib/api/types";
import { unwrap } from "@/lib/api/unwrap";

/** The signed-in user, or null when signed out (401). */
export async function fetchMe(client: ApiClient = api): Promise<Me | null> {
  try {
    return await unwrap(client.GET("/api/v1/me"));
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export const meQuery = (client: ApiClient = api) =>
  queryOptions({ queryKey: ["me"] as const, queryFn: () => fetchMe(client), staleTime: 60_000 });

export function useMe(client?: ApiClient) {
  return useQuery(meQuery(client));
}

export function hasPermission(me: Me | null | undefined, permission: string): boolean {
  return !!me && me.permissions.includes(permission);
}

/** Switch the active organization (stored by the BFF in an httpOnly cookie). */
export async function switchOrganization(organizationId: string | null): Promise<void> {
  const response = await fetch("/auth/org", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ organizationId }),
  });
  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      code: "organization_switch_failed",
      message: "Could not switch organization.",
    });
  }
}
