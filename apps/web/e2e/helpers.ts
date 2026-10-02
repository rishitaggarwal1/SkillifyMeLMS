import fs from "node:fs";
import path from "node:path";

import { expect, type Page } from "@playwright/test";

/** DEV_USER_PASSWORD from the environment, else from the repo-root .env (local runs). */
function devUserPassword(): string {
  const fromEnv = process.env.DEV_USER_PASSWORD;
  if (fromEnv) return fromEnv;
  const envFile = path.resolve("../../.env"); // Playwright runs from apps/web
  const line = fs.existsSync(envFile)
    ? fs
        .readFileSync(envFile, "utf-8")
        .split(/\r?\n/)
        .find((l) => l.startsWith("DEV_USER_PASSWORD="))
    : undefined;
  const value = line
    ?.slice("DEV_USER_PASSWORD=".length)
    .trim()
    .replace(/^"(.*)"$/, "$1");
  if (!value) throw new Error("DEV_USER_PASSWORD is not set (see .env.example)");
  return value;
}

/** Password of the dev users `make seed` creates (SEED_DEV_USERS=true). Local-only. */
export const DEV_PASSWORD = devUserPassword();

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
