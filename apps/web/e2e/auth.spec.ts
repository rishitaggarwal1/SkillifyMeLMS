import { expect, test } from "@playwright/test";

import { signIn, signInOnKeycloak } from "./helpers";

test("protected pages send you through Keycloak and back", async ({ page }) => {
  await page.goto("/admin/batches");
  // proxy.ts -> /auth/login -> Keycloak's own login page
  await signInOnKeycloak(page, "admin@demo-college.local");
  await page.waitForURL("**/admin/batches");

  await expect(page.getByRole("heading", { level: 1, name: "Batches" })).toBeVisible();
  // The org's batches load after the round trip. (Not a specific seeded batch: admin-import adds
  // batches on every run, so a seeded one eventually moves off the first page.)
  await expect(
    page.getByRole("list", { name: "Batches" }).getByRole("listitem").first(),
  ).toContainText("members");
});

test("tokens live only in encrypted httpOnly cookies", async ({ page, context }) => {
  await signIn(page, "admin@demo-college.local", "/admin/batches");

  const cookies = await context.cookies();
  const session = cookies.filter((c) => c.name.startsWith("__Host-sm_"));
  expect(session.map((c) => c.name).sort()).toEqual(
    expect.arrayContaining(["__Host-sm_at", "__Host-sm_it", "__Host-sm_rt"]),
  );
  for (const cookie of session) {
    expect(cookie.httpOnly).toBe(true);
    expect(cookie.secure).toBe(true);
    expect(cookie.sameSite).toBe("Lax");
    expect(cookie.value.startsWith("eyJ")).toBe(false); // not a readable JWT
  }
  const visibleToScripts = await page.evaluate(() => ({
    cookie: document.cookie,
    storage: JSON.stringify({ ...localStorage, ...sessionStorage }),
  }));
  expect(visibleToScripts.cookie).not.toContain("sm_");
  expect(visibleToScripts.storage).not.toMatch(/eyJ[\w-]+\.[\w-]+\./); // no JWTs in storage
});

test("sign out ends the session", async ({ page }) => {
  await signIn(page, "admin@demo-college.local", "/admin/batches");
  await page.getByTestId("sign-out").click();
  await page.waitForURL((url) => url.pathname === "/");
  await expect(page.getByTestId("sign-in")).toBeVisible();

  // The Keycloak SSO session is gone too: protected pages ask for credentials again.
  await page.goto("/admin/batches");
  await expect(page.locator("#username")).toBeVisible();
});

test("the org switcher changes the active organization", async ({ page }) => {
  // multi@ is an instructor in both SkillifyMe and Demo College.
  await signIn(page, "multi@skillifyme.local", "/");
  const switcher = page.getByRole("combobox", { name: "Organization" });
  await expect(switcher).toHaveValue("");

  await switcher.selectOption({ label: "Demo College" });
  await expect(switcher).not.toHaveValue("");
  await page.reload();
  await expect(page.getByRole("combobox", { name: "Organization" })).toHaveValue(
    await switcher.inputValue(),
  );
  await expect(
    page.getByRole("combobox", { name: "Organization" }).locator("option:checked"),
  ).toHaveText("Demo College");
});

test("instructors don't get the admin area", async ({ page }) => {
  await signIn(page, "instructor@demo-college.local", "/admin/batches");
  await expect(page.getByText("This area is for organization admins.")).toBeVisible();
});
