import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.WEB_PORT ?? 3000);
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;

/**
 * E2E tests run against a real API (docker-compose locally, service containers in CI).
 * Locally, an already-running `make dev` web server is reused.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  // A local dev server compiles on the same event loop that serves requests; don't swamp it.
  workers: process.env.CI ? undefined : 2,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  globalSetup: "./e2e/global-setup.ts",
  // Locally tests usually hit the dev server (slow first compiles); CI uses the production build.
  timeout: process.env.CI ? 30_000 : 60_000,
  expect: { timeout: process.env.CI ? 5_000 : 15_000 },
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [
    // Mobile first: most students use low-end Android phones.
    { name: "mobile-chrome", use: { ...devices["Pixel 7"] } },
    { name: "desktop-chrome", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: "pnpm start:standalone",
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { PORT: String(PORT), HOSTNAME: "127.0.0.1" },
  },
});
