import { expect, test } from "@playwright/test";

test("home page shows the API and its dependencies as healthy", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1, name: "SkillifyMe Portal" })).toBeVisible();
  await expect(page.getByTestId("overall-status")).toHaveText("All systems operational");
  await expect(page.getByTestId("check-database")).toContainText("OK");
  await expect(page.getByTestId("check-redis")).toContainText("OK");
});

test("backend proxy only exposes allow-listed API paths", async ({ request }) => {
  const ok = await request.get("/backend/health/live");
  expect(ok.status()).toBe(200);
  expect(ok.headers()["x-request-id"]).toBeTruthy();

  const blocked = await request.get("/backend/docs");
  expect(blocked.status()).toBe(404);
  expect(await blocked.json()).toMatchObject({ error: { code: "not_found" } });
});
