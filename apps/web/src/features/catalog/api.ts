import "server-only";

import type { components } from "@/lib/api/schema";

import { CATALOG_TAG } from "./tags";

export type CatalogEntry = components["schemas"]["CatalogEntryOut"];

/** Time-based fallback if an on-demand revalidation is ever missed (seconds). */
export const CATALOG_REVALIDATE_SECONDS = 300;
const PAGE_SIZE = 100;

function apiUrl(path: string): string {
  const base = (process.env.API_INTERNAL_URL ?? "http://localhost:8000").replace(/\/+$/, "");
  return `${base}/api/v1${path}`;
}

/**
 * `next build` runs without the API (in the Docker build). Prerender an empty catalog then; the
 * first request after the time-based window, or the first on-demand revalidation, renders the
 * real one. At runtime errors are thrown instead, so Next keeps serving the last good page.
 */
const isBuild = () => process.env.NEXT_PHASE === "phase-production-build";

async function getJson<T>(path: string): Promise<T | null> {
  let response: Response;
  try {
    response = await fetch(
      apiUrl(path),
      // Development always reads fresh: `next dev` keeps tagged fetches in its data cache too,
      // and the API's revalidation call can't reach a web process running on the host.
      process.env.NODE_ENV === "development"
        ? { cache: "no-store" }
        : { next: { tags: [CATALOG_TAG], revalidate: CATALOG_REVALIDATE_SECONDS } },
    );
  } catch (error) {
    if (isBuild()) return null;
    throw error;
  }
  if (response.status === 404) return null;
  if (!response.ok) {
    if (isBuild()) return null;
    throw new Error(`Catalog API ${path}: HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

/** The first page of the public catalog (newest first). */
export async function listCatalog(): Promise<CatalogEntry[]> {
  const page = await getJson<{ items: CatalogEntry[] }>(`/catalog?limit=${PAGE_SIZE}`);
  return page?.items ?? [];
}

export async function getCatalogEntry(slug: string): Promise<CatalogEntry | null> {
  if (!/^[a-z0-9-]{1,160}$/.test(slug)) return null;
  return getJson<CatalogEntry>(`/catalog/${slug}`);
}
