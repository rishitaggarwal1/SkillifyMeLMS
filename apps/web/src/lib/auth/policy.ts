/** Small, pure security decisions used by the BFF routes (unit-tested). */

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * CSRF defence for state-changing requests: the browser must say the request comes from our own
 * origin. Browsers always send `Origin` on cross-origin requests and on same-origin non-GET
 * requests, so a missing Origin on an unsafe method is rejected too. SameSite=Lax cookies are the
 * first layer; this check is the second.
 */
export function isAllowedOrigin(
  method: string,
  headers: { get(name: string): string | null },
  webOrigin: string,
): boolean {
  if (!UNSAFE_METHODS.has(method.toUpperCase())) return true;
  const fetchSite = headers.get("sec-fetch-site");
  if (fetchSite && fetchSite !== "same-origin") return false;
  return headers.get("origin") === webOrigin;
}

/**
 * Only allow relative, same-app paths as post-login destinations (no open redirects):
 * "/admin/batches?x=1" is fine; "//evil.com", "https://evil.com", "/\\evil.com" are not.
 */
export function safeReturnTo(value: string | null | undefined, fallback = "/"): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return fallback;
  }
  try {
    const url = new URL(value, "http://placeholder.invalid");
    if (url.origin !== "http://placeholder.invalid") return fallback;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return fallback;
  }
}

/** Reads `exp` from a JWT without verifying it (the API verifies; this only schedules refresh). */
export function tokenExpiresAt(token: string): number | null {
  const payload = token.split(".")[1];
  if (!payload) return null;
  try {
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as {
      exp?: unknown;
    };
    return typeof json.exp === "number" ? json.exp : null;
  } catch {
    return null;
  }
}

/** Refresh when the access token is missing, unreadable, or expires within `skewSeconds`. */
export function needsRefresh(
  accessToken: string | null,
  nowSeconds: number,
  skewSeconds = 30,
): boolean {
  if (!accessToken) return true;
  const exp = tokenExpiresAt(accessToken);
  return exp === null || exp - skewSeconds <= nowSeconds;
}

export function isUuid(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
  );
}
