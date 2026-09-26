import { chromium } from "@playwright/test";

/**
 * Warm the server before tests. A dev server (local `make dev`) compiles each route and client
 * bundle on first request, which on Windows bind mounts can take several seconds and would eat
 * into test timeouts. CI runs the production build, where this is nearly free.
 */
export default async function globalSetup(): Promise<void> {
  const base = process.env.E2E_BASE_URL ?? `http://localhost:${process.env.WEB_PORT ?? 3000}`;
  for (const path of ["/backend/health/ready", "/backend/api/v1/me", "/auth/login"]) {
    try {
      await fetch(`${base}${path}`, { redirect: "manual", signal: AbortSignal.timeout(60_000) });
    } catch {
      // The webServer may still be starting; tests will surface real problems.
    }
  }
  // Load the home page in a real browser so its client bundles are compiled and hydrate.
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.goto(base, { timeout: 90_000 });
    await page
      .getByTestId("overall-status")
      .filter({ hasNotText: "Checking" })
      .waitFor({ timeout: 90_000 });
  } catch {
    // Best effort.
  } finally {
    await browser.close();
  }
}
