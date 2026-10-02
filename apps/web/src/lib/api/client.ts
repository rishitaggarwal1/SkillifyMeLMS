import createClient from "openapi-fetch";

import type { paths } from "./schema";

/**
 * Typed API client. Types come from the FastAPI OpenAPI spec (`make gen-api`).
 *
 * - In the browser, requests go same-origin to `/backend/*`, which the Next.js route handler proxies
 *   to the API. This keeps auth cookies httpOnly and first-party (no tokens in JS / localStorage)
 *   and avoids CORS.
 * - On the server, requests go straight to API_INTERNAL_URL.
 */
export const BROWSER_API_BASE = "/backend";

export function apiBaseUrl(): string {
  if (typeof window !== "undefined") return BROWSER_API_BASE;
  const url = process.env.API_INTERNAL_URL;
  if (!url) throw new Error("API_INTERNAL_URL is not set");
  return url;
}

export function createApiClient(
  baseUrl: string = apiBaseUrl(),
  fetchImpl?: (request: Request) => Promise<Response>,
) {
  return createClient<paths>({
    baseUrl,
    credentials: "same-origin",
    ...(fetchImpl ? { fetch: fetchImpl } : {}),
  });
}

export type ApiClient = ReturnType<typeof createApiClient>;

// The shared client is created on import, and `next build` imports client components without the
// runtime environment. So a missing API_INTERNAL_URL fails the first server-side request instead
// of the import.
const UNSET_BASE = "http://api-internal-url-not-set.invalid";

export const api = createApiClient(
  typeof window !== "undefined" ? BROWSER_API_BASE : process.env.API_INTERNAL_URL || UNSET_BASE,
  async (request) => {
    if (request.url.startsWith(UNSET_BASE)) throw new Error("API_INTERNAL_URL is not set");
    return fetch(request);
  },
);
