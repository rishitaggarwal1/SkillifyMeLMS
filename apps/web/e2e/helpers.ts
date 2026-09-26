import { expect, type Page } from "@playwright/test";

/** Password for the dev-realm test users (infra/local/keycloak/realm). Local-only. */
export const DEV_PASSWORD = "Local-Dev-Only-1";

/** Fill in the real Keycloak login page (we arrive there via /auth/login). */
export async function signInOnKeycloak(page: Page, email: string): Promise<void> {
  await expect(page.locator("#username")).toBeVisible();
  await page.locator("#username").fill(email);
  await page.locator("#password").fill(DEV_PASSWORD);
  await page.locator("#kc-login").click();
}

/** Start at `path` (a protected page), sign in, and wait to land back on it. */
export async function signIn(page: Page, email: string, path = "/"): Promise<void> {
  await page.goto(`/auth/login?returnTo=${encodeURIComponent(path)}`);
  await signInOnKeycloak(page, email);
  await page.waitForURL((url) => url.pathname === path.split("?")[0]);
}
