/**
 * After `make seed-demo`: each demo login works and lands where that role works, with the demo
 * data in place. Read-only (nothing is submitted or graded), so it can run against a live demo.
 *
 * Passwords come from the credentials file the seed wrote (DEMO_CREDENTIALS_FILE, default
 * ../../.secrets/demo-credentials.txt). Tracing is off here so no report ever records one.
 */
import fs from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

const FILE =
  process.env.DEMO_CREDENTIALS_FILE ?? path.resolve("../../.secrets/demo-credentials.txt");

function logins(): Map<string, string> {
  const found = new Map<string, string>();
  for (const line of fs.readFileSync(FILE, "utf-8").split("\n")) {
    const [, email, password] = line.split("\t");
    if (!line.startsWith("#") && email?.includes("@") && password) found.set(email, password);
  }
  return found;
}

test.use({ trace: "off", viewport: { width: 360, height: 780 } });
test.skip(!fs.existsSync(FILE), "run `make seed-demo` first");

async function signInAs(page: Page, local: string) {
  const email = `demo.${local}@skillifyme.co.in`;
  const password = logins().get(email);
  expect(password, `${email} is in the credentials file`).toBeTruthy();
  await page.goto("/auth/login?returnTo=%2F");
  await page.locator("#username").fill(email);
  await page.locator("#password").fill(password!);
  await page.locator("#kc-login").click();
}

test.describe("demo logins", () => {
  test("platform admin", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chrome", "one run is enough");
    await signInAs(page, "platform-admin");
    await page.waitForURL((url) => url.pathname.startsWith("/platform"));
    await expect(page.getByRole("heading", { name: "Users by organization role" })).toBeVisible();
  });

  test("college admin", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chrome", "one run is enough");
    await signInAs(page, "admin");
    await page.waitForURL((url) => url.pathname.startsWith("/admin"));
    await page.goto("/admin/courses");
    await expect(
      page.getByRole("list", { name: "Granted courses" }).getByText("Python Foundations"),
    ).toBeVisible();
  });

  test("instructor", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chrome", "one run is enough");
    await signInAs(page, "instructor");
    await page.waitForURL((url) => url.pathname.startsWith("/teach"));
    await page
      .getByRole("region", { name: "Assigned courses" })
      .getByRole("link", { name: /Python Foundations/ })
      .click();
    await page
      .getByRole("list", { name: "Assignments to grade" })
      .getByRole("link", { name: /FizzBuzz/ })
      .click();
    // Two of the three submissions are waiting (Priya and Ananya); Aarav's is graded.
    const rows = page.getByRole("list", { name: "Submissions" }).getByRole("listitem");
    await expect(rows.filter({ hasText: "Priya Sharma" })).toContainText("To grade");
    await expect(rows.filter({ hasText: "Ananya Iyer" })).toContainText("To grade");
  });

  test("student", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chrome", "one run is enough");
    await signInAs(page, "student");
    await page.waitForURL((url) => url.pathname.startsWith("/learn"));
    await expect(page.getByRole("link", { name: /Python Foundations/ })).toContainText(
      "83% complete",
    );
  });
});
