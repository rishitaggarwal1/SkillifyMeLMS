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

export const api = createApiClient();
