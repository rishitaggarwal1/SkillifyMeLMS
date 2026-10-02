/**
 * Phase 2.5 college admin: a granted course is assigned to a batch from /admin/courses (on a
 * phone), its students then have it, and staff see progress per batch: the batch's "Courses and
 * progress" and the instructor's progress table with its CSV.
 */
import { expect, test, type Browser, type Page } from "@playwright/test";

import { signIn } from "./helpers";

const DESKTOP = { width: 1280, height: 800 };
const PHONE = { width: 360, height: 780 };

async function newUser(browser: Browser, email: string, path: string, viewport = DESKTOP) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await signIn(page, email, path);
  return { context, page };
}

async function noSideScroll(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    page.viewportSize()!.width,
  );
}

test("a college admin assigns a granted course and staff see progress", async ({
  browser,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chrome", "one run is enough (it sets viewports)");
  test.setTimeout(240_000);
  const title = `Granted ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

  // The publisher publishes a one-lesson course and grants it to Demo College.
  const author = await newUser(browser, "author@skillifyme.local", "/teach/courses");
  const a = author.page;
  await a.getByRole("button", { name: "New course" }).click();
  await a.getByLabel("Title").fill(title);
  await a.getByRole("button", { name: "Create course" }).click();
  await expect(a.getByRole("heading", { level: 1, name: title })).toBeVisible();
  const courseId = a.url().split("/").at(-1)!;
  await a.getByLabel("New module").fill("Basics");
  await a.getByRole("button", { name: "Add module" }).click();
  const add = a.getByRole("form", { name: "Add lesson to Basics" });
  await add.getByPlaceholder("Lesson title").fill("Welcome");
  await add.getByLabel("Type").selectOption("notes");
  await add.getByRole("button", { name: "Add lesson" }).click();
  await expect(a.getByRole("link", { name: "Welcome" })).toBeVisible();
  await a.getByRole("button", { name: "Publish…" }).click();
  await a.getByRole("dialog").getByRole("button", { name: "Publish", exact: true }).click();
  await expect(a.getByText("Published v1.0").first()).toBeVisible();
  await a.getByLabel("Grant to an organization").fill("Demo College");
  await a
    .getByRole("list", { name: "Organization search results" })
    .getByRole("listitem")
    .filter({ hasText: "Demo College" })
    .getByRole("button", { name: "Grant" })
    .click();
  await expect(
    a.getByRole("list", { name: "Organization grants" }).filter({ hasText: "Demo College" }),
  ).toBeVisible();
  await author.context.close();

  // The college admin assigns it to ECE 2026 from /admin/courses, on a phone.
  const admin = await newUser(browser, "admin@demo-college.local", "/admin/courses", PHONE);
  const d = admin.page;
  const course = d.getByRole("list", { name: "Granted courses" }).getByRole("listitem").filter({
    hasText: title,
  });
  await course.getByRole("button", { name: "Choose batches" }).click();
  const ece = course.getByRole("checkbox", { name: /^ECE 2026/ });
  const boxes = course.getByRole("list", { name: "Batches" }).getByRole("checkbox");
  const loadMore = course.getByRole("button", { name: "Load more" });
  await expect(boxes.first()).toBeVisible();
  while ((await ece.count()) === 0 && (await loadMore.count()) > 0) {
    const before = await boxes.count();
    await loadMore.click();
    await expect.poll(() => boxes.count()).toBeGreaterThan(before);
  }
  await ece.click();
  await expect(ece).toBeChecked();
  await noSideScroll(d);

  // The batch's page lists the course with its progress.
  await d.getByRole("navigation", { name: "Admin" }).getByRole("link", { name: "Batches" }).click();
  const batchId = await d.evaluate(async () => {
    const response = await fetch("/backend/api/v1/batches?name=ECE%202026");
    return ((await response.json()) as { items: { id: string }[] }).items[0]!.id;
  });
  await d.goto(`/admin/batches/${batchId}`);
  const courses = d.getByRole("list", { name: "Batch courses" });
  await expect(courses.getByRole("link", { name: new RegExp(title) })).toBeVisible({
    timeout: 30_000,
  });
  await noSideScroll(d);
  await admin.context.close();

  // The ECE student now has the course (enrollment fan-out runs in the worker).
  const student = await newUser(browser, "ece.student@demo-college.local", "/learn", PHONE);
  await expect(student.page.getByRole("link", { name: new RegExp(title) })).toBeVisible({
    timeout: 30_000,
  });
  await student.context.close();

  // The college's instructor sees the ECE batch's progress table and can download it.
  const instructor = await newUser(
    browser,
    "instructor@demo-college.local",
    `/teach/courses/${courseId}/progress?batch=${batchId}`,
    PHONE,
  );
  const t = instructor.page;
  await expect(t.getByLabel("Batch")).toHaveValue(batchId);
  const table = t.getByRole("region", { name: "Progress table" });
  await expect(table.getByRole("columnheader", { name: "Welcome" })).toBeVisible();
  await expect(
    table.getByRole("row").filter({ hasText: "ece.student@demo-college.local" }),
  ).toContainText("0%");
  await noSideScroll(t); // only the table scrolls sideways
  const download = t.waitForEvent("download");
  await t.getByRole("link", { name: "Download CSV" }).click();
  const csv = await download;
  expect(csv.suggestedFilename()).toMatch(/^progress-.*\.csv$/);
  await instructor.context.close();
});
