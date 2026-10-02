/**
 * Phase 2.5 platform admin: the role-aware home, the dashboard, and creating a college with its
 * first admin (whose invitation email arrives in Mailpit). On the mobile project every page is
 * checked at 360px.
 */
import { expect, test, type Page } from "@playwright/test";

import { signIn, signInOnKeycloak } from "./helpers";

const MAILPIT = `http://localhost:${process.env.MAILPIT_UI_PORT ?? "8025"}`;

async function phoneWidth(page: Page, isMobile: boolean) {
  if (isMobile) await page.setViewportSize({ width: 360, height: 780 });
}

async function noSideScroll(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    page.viewportSize()!.width,
  );
}

test("home sends each role to its area and shows it in the header", async ({ page, isMobile }) => {
  await phoneWidth(page, isMobile);
  for (const [email, path, label] of [
    ["platform.admin@skillifyme.local", "/platform", "Platform admin"],
    ["admin@demo-college.local", "/admin", "Org admin"],
    ["instructor@demo-college.local", "/teach", "Instructor"],
    ["cse.student@demo-college.local", "/learn", "Student"],
  ] as const) {
    await page.context().clearCookies();
    await page.goto("/auth/login?returnTo=%2F");
    await signInOnKeycloak(page, email);
    await page.waitForURL((url) => url.pathname.startsWith(path));
    await expect(page.getByTestId("active-role")).toContainText(label);
    await noSideScroll(page);
  }
});

test("someone with several roles chooses where to work", async ({ page, isMobile }) => {
  await phoneWidth(page, isMobile);
  // multi@ is an instructor in SkillifyMe and in Demo College.
  await signIn(page, "multi@skillifyme.local", "/");
  const choices = page.getByRole("list", { name: "Your roles" }).getByRole("button");
  await expect(choices).toHaveCount(2);
  await choices.filter({ hasText: "Demo College" }).click();
  await page.waitForURL((url) => url.pathname.startsWith("/teach"));
  await expect(page.getByTestId("active-role")).toContainText("Instructor");
  await expect(page.getByTestId("active-role")).toContainText("Demo College");
});

test("a platform admin creates a college and invites its admin", async ({ page, isMobile }) => {
  test.setTimeout(90_000);
  await phoneWidth(page, isMobile);
  const tag = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  const college = `Shivaji College ${tag}`;
  const adminEmail = `principal.${tag}@shivaji-college.test`;

  await signIn(page, "platform.admin@skillifyme.local", "/platform");
  await expect(page.getByRole("heading", { name: "Users by organization role" })).toBeVisible();
  await expect(page.getByText("Platform admins aren't included")).toBeVisible();
  await noSideScroll(page);

  await page
    .getByRole("navigation", { name: "Platform" })
    .getByRole("link", { name: "Organizations" })
    .click();
  await page.getByRole("button", { name: "New organization" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name", { exact: true }).fill(college);
  await expect(dialog.getByLabel("Slug", { exact: true })).toHaveValue(`shivaji-college-${tag}`);
  await dialog.getByRole("button", { name: "Create organization" }).click();
  await expect(page.getByRole("heading", { level: 1, name: college })).toBeVisible();
  await noSideScroll(page);

  const invite = page.getByRole("form", { name: "Invite an org admin" });
  await invite.getByLabel("Email").fill(adminEmail);
  await invite.getByLabel("Full name (optional)").fill("Meera Kulkarni");
  await invite.getByRole("button", { name: "Send invitation" }).click();
  const admins = page.getByRole("list", { name: "Org admins" });
  await expect(admins.getByRole("listitem").filter({ hasText: adminEmail })).toContainText(
    "Invited",
  );

  // Keycloak sent the set-password email (Mailpit locally and in CI).
  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${MAILPIT}/api/v1/search`, {
          params: { query: `to:${adminEmail}` },
        });
        return ((await response.json()) as { messages_count?: number }).messages_count ?? 0;
      },
      { timeout: 30_000 },
    )
    .toBeGreaterThan(0);

  // The new college shows up across the platform screens.
  await page
    .getByRole("navigation", { name: "Platform" })
    .getByRole("link", { name: "Users" })
    .click();
  await page.getByLabel("Search users").fill(adminEmail);
  await expect(page.getByRole("list", { name: "Users" })).toContainText(`${college}: Org admin`);
  await page.getByRole("list", { name: "Users" }).getByRole("link").first().click();
  await expect(page.getByRole("list", { name: "Memberships" })).toContainText(college);
  await noSideScroll(page);

  await page
    .getByRole("navigation", { name: "Platform" })
    .getByRole("link", { name: "Audit log" })
    .click();
  await page.getByLabel("Action").fill("invitation.created");
  await expect(
    page.getByRole("list", { name: "Audit entries" }).getByRole("listitem").first(),
  ).toContainText("invitation.created");
  await noSideScroll(page);
});

test("platform pages are for platform admins only", async ({ page }) => {
  await signIn(page, "admin@demo-college.local", "/platform/users");
  await expect(page.getByText("This area is for platform admins.")).toBeVisible();
});
