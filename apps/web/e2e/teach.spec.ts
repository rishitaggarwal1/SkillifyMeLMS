import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { signIn } from "./helpers";

const lessonsIn = (page: Page, moduleTitle: string) =>
  page.getByRole("list", { name: `Lessons in ${moduleTitle}` }).getByRole("listitem");

async function addLesson(page: Page, moduleTitle: string, title: string, type: string) {
  const form = page.getByRole("form", { name: `Add lesson to ${moduleTitle}` });
  await form.getByPlaceholder("Lesson title").fill(title);
  await form.getByLabel("Type").selectOption(type);
  await form.getByRole("button", { name: "Add lesson" }).click();
  await expect(lessonsIn(page, moduleTitle).filter({ hasText: title })).toHaveCount(1);
}

test("an instructor builds, orders, writes, publishes and grants a course", async ({ page }) => {
  test.setTimeout(120_000);
  const title = `Builder course ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  await signIn(page, "author@skillifyme.local", "/teach/courses");

  // Create the course.
  await page.getByRole("button", { name: "New course" }).click();
  await page.getByLabel("Title").fill(title);
  await page.getByRole("button", { name: "Create course" }).click();
  await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
  await expect(page.getByText("Not published yet")).toBeVisible();
  const courseUrl = page.url();

  // Outline: one module with notes, PDF and a placeholder quiz.
  await page.getByLabel("New module").fill("Basics");
  await page.getByRole("button", { name: "Add module" }).click();
  await expect(page.getByRole("heading", { level: 3, name: "1. Basics" })).toBeVisible();
  await addLesson(page, "Basics", "Intro notes", "notes");
  await addLesson(page, "Basics", "Handout", "pdf");
  await addLesson(page, "Basics", "Practice quiz", "quiz");
  await expect(lessonsIn(page, "Basics").filter({ hasText: "Practice quiz" })).toContainText(
    "Coming soon",
  );

  // Reorder (the button path; drag-and-drop uses the same edit) and check it persisted.
  await page.getByRole("button", { name: "Move lesson Handout up" }).click();
  await expect(lessonsIn(page, "Basics").first()).toContainText("Handout");
  await page.reload();
  await expect(lessonsIn(page, "Basics").nth(0)).toContainText("Handout");
  await expect(lessonsIn(page, "Basics").nth(1)).toContainText("Intro notes");

  // Small screens: the outline never scrolls sideways.
  await page.setViewportSize({ width: 360, height: 780 });
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(360);
  await page.setViewportSize({ width: 1280, height: 800 });

  // Notes: write in the (lazy-loaded) editor, save, and see it after a reload.
  await page.getByRole("link", { name: "Intro notes" }).click();
  const editor = page.getByRole("textbox", { name: "Notes" });
  await editor.click();
  await editor.pressSequentially("Two pointers");
  await page.getByRole("button", { name: "H2" }).click(); // formats the current line
  await expect(editor.locator("h2")).toHaveText("Two pointers");
  await editor.press("End");
  await editor.press("Enter");
  await editor.pressSequentially("Walk inward from both ends.");
  await page.getByRole("button", { name: "Save notes" }).click();
  await expect(page.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Notes" }).locator("h2")).toHaveText(
    "Two pointers",
  );

  // PDF: upload through the presigned POST, confirmed by the API.
  await page.goto(courseUrl);
  await page.getByRole("link", { name: "Handout" }).click();
  await page.getByLabel("Upload a PDF").setInputFiles(path.resolve("e2e/fixtures/handout.pdf"));
  await expect(page.getByText("handout.pdf")).toBeVisible({ timeout: 30_000 });

  // Publish the first release.
  await page.goto(courseUrl);
  await page.getByRole("button", { name: "Publish…" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("This is the first release")).toBeVisible();
  await dialog.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(page.getByText("Published v1.0")).toBeVisible();

  // Grant it to another organization through the publisher's directory.
  await page.getByLabel("Grant to an organization").fill("Demo College");
  const results = page.getByRole("list", { name: "Organization search results" });
  await results
    .getByRole("listitem")
    .filter({ hasText: "Demo College" })
    .getByRole("button", { name: "Grant" })
    .click();
  await expect(
    page.getByRole("list", { name: "Organization grants" }).filter({ hasText: "Demo College" }),
  ).toBeVisible();

  // The course is listed with its version.
  await page.goto("/teach/courses");
  await expect(
    page
      .getByRole("region", { name: "Your courses" })
      .getByRole("link", { name: new RegExp(title) }),
  ).toContainText("v1.0");
});

test("an assigned org's instructor can read but not edit", async ({ page }) => {
  await signIn(page, "instructor@demo-college.local", "/teach/courses");
  await expect(page.getByRole("button", { name: "New course" })).toBeVisible(); // own courses
  const assigned = page.getByRole("region", { name: "Assigned courses" });
  await expect(assigned).toBeVisible();
  const first = assigned.getByRole("link").first();
  if ((await first.count()) === 0) return; // nothing assigned to Demo College yet
  await first.click();
  await expect(page.getByText("only the publisher can edit it")).toBeVisible();
  await expect(page.getByRole("button", { name: "Publish…" })).toHaveCount(0);
  await expect(page.getByRole("form", { name: "Add module" })).toHaveCount(0);
});
