/**
 * Phase 2 "done when" (docs/plans/phase-2.md): an instructor builds and publishes a course with
 * video, notes and PDF lessons; a student in an assigned batch completes it with correct progress.
 * Everything here goes through the UI the way people use it, with no API shortcuts:
 *
 * 1. the publisher (author@skillifyme.local) builds the course and publishes it;
 * 2. grants it to Demo College;
 * 3. the Demo College admin assigns it to CSE 2026;
 * 4. cse.student@ completes it on a 360px phone, with progress checked at each step;
 * 5. ece.student@ (same college, other batch) never sees it.
 */
import path from "node:path";

import { expect, test, type Browser, type Page } from "@playwright/test";

import { signIn } from "./helpers";

const DESKTOP = { width: 1280, height: 800 };
const PHONE = { width: 360, height: 780 };
// Video completion waits for the server-side progress flush (Celery beat: 30 s locally).
const FLUSH_WAIT = 120_000;

async function newUser(browser: Browser, email: string, path_: string, viewport = DESKTOP) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await signIn(page, email, path_);
  return { context, page };
}

const lessonsIn = (page: Page, moduleTitle: string) =>
  page.getByRole("list", { name: `Lessons in ${moduleTitle}` }).getByRole("listitem");

async function addLesson(page: Page, title: string, type: string) {
  const form = page.getByRole("form", { name: "Add lesson to Arrays" });
  await form.getByPlaceholder("Lesson title").fill(title);
  await form.getByLabel("Type").selectOption(type);
  await form.getByRole("button", { name: "Add lesson" }).click();
  await expect(lessonsIn(page, "Arrays").filter({ hasText: title })).toHaveCount(1);
}

test("an instructor publishes a course and a student in the assigned batch completes it", async ({
  browser,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== "mobile-chrome",
    "one full run is enough (it sets its own viewports)",
  );
  test.setTimeout(360_000);
  const title = `Done-when ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

  // ---------------------------------------------------------------- 1. build and publish
  const author = await newUser(browser, "author@skillifyme.local", "/teach/courses");
  const a = author.page;
  await a.getByRole("button", { name: "New course" }).click();
  await a.getByLabel("Title").fill(title);
  await a.getByRole("button", { name: "Create course" }).click();
  await expect(a.getByRole("heading", { level: 1, name: title })).toBeVisible();
  const courseUrl = a.url();
  const courseId = courseUrl.split("/").at(-1)!;

  await a.getByLabel("New module").fill("Arrays");
  await a.getByRole("button", { name: "Add module" }).click();
  await addLesson(a, "Watch: arrays", "video");
  await addLesson(a, "Read: two pointers", "notes");
  await addLesson(a, "Handout", "pdf");

  // Video: upload the MP4 fixture (local provider) and attach it once processed.
  await a.getByRole("link", { name: "Watch: arrays" }).click();
  const videoSection = a.getByRole("region", { name: "Lesson video" });
  await videoSection.getByRole("button", { name: "Upload a new video" }).click();
  await videoSection.getByLabel("Title").fill(`${title} video`);
  await videoSection
    .getByLabel("MP4 video")
    .setInputFiles(path.resolve("../api/tests/fixtures/video.mp4"));
  await videoSection.getByRole("button", { name: "Upload video", exact: true }).click();
  await expect(videoSection.getByLabel("Ready videos")).not.toHaveValue("", { timeout: 60_000 });

  // Notes: written in the editor.
  await a.goto(courseUrl);
  await a.getByRole("link", { name: "Read: two pointers" }).click();
  const editor = a.getByRole("textbox", { name: "Notes" });
  await editor.click();
  await editor.pressSequentially("Two pointers");
  await a.getByRole("button", { name: "H2" }).click();
  await editor.press("End");
  await editor.press("Enter");
  await editor.pressSequentially("Walk inward from both ends of a sorted array.");
  await a.getByRole("button", { name: "Save notes" }).click();
  await expect(a.getByRole("status").filter({ hasText: "All changes saved" })).toBeVisible();

  // PDF: uploaded through the presigned POST and confirmed.
  await a.goto(courseUrl);
  await a.getByRole("link", { name: "Handout" }).click();
  await a.getByLabel("Upload a PDF").setInputFiles(path.resolve("e2e/fixtures/handout.pdf"));
  await expect(a.getByText("handout.pdf")).toBeVisible({ timeout: 30_000 });

  await a.goto(courseUrl);
  await a.getByRole("button", { name: "Publish…" }).click();
  await a.getByRole("dialog").getByRole("button", { name: "Publish", exact: true }).click();
  await expect(a.getByText("Published v1.0").first()).toBeVisible();

  // ---------------------------------------------------------------- 2. grant to Demo College
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

  // ---------------------------------------------------------------- 3. distribute to CSE 2026
  const admin = await newUser(browser, "admin@demo-college.local", "/teach/courses");
  const d = admin.page;
  await d
    .getByRole("region", { name: "Assigned courses" })
    .getByRole("link", { name: new RegExp(title) })
    .click();
  await expect(d.getByText("only the publisher can edit it")).toBeVisible();
  const cse = d.getByRole("checkbox", { name: /^CSE 2026/ });
  // Other specs add batches on every run: page through the list until the seeded batch shows.
  const batchBoxes = d.getByRole("list", { name: "Batches" }).getByRole("checkbox");
  const loadMore = d.getByRole("button", { name: "Load more" });
  await expect(batchBoxes.first()).toBeVisible();
  while ((await cse.count()) === 0 && (await loadMore.count()) > 0) {
    const before = await batchBoxes.count();
    await loadMore.click();
    await expect.poll(() => batchBoxes.count()).toBeGreaterThan(before);
  }
  await cse.click();
  await expect(cse).toBeChecked();
  await admin.context.close();

  // ---------------------------------------------------------------- 4. the student completes it
  const student = await newUser(browser, "cse.student@demo-college.local", "/learn", PHONE);
  const s = student.page;
  const card = s.getByRole("link", { name: new RegExp(title) });
  await expect(card).toBeVisible({ timeout: 30_000 }); // enrollment fan-out runs in the worker
  await expect(card).toContainText("0% complete");
  await card.click();
  await expect(s.getByRole("heading", { level: 2, name: "Watch: arrays" })).toBeVisible();
  expect(await s.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);

  // Watch the whole (12 s) video at 2x; completion lands after the server's progress flush.
  const video = s.getByLabel("Lesson video");
  await expect(video).toBeVisible();
  await video.evaluate(async (element: HTMLVideoElement) => {
    element.muted = true;
    element.playbackRate = 2;
    await element.play();
  });
  await expect
    .poll(() => video.evaluate((element: HTMLVideoElement) => element.ended), { timeout: 30_000 })
    .toBe(true);
  await expect(s.getByText("✓ Completed")).toBeVisible({ timeout: FLUSH_WAIT });
  await expect(s.getByText("33% complete")).toBeVisible({ timeout: FLUSH_WAIT });

  await s.getByRole("link", { name: "Next →" }).click();
  await expect(s.getByRole("heading", { level: 2, name: "Read: two pointers" })).toBeVisible();
  await expect(
    s
      .getByRole("article", { name: "Lesson" })
      .getByRole("heading", { name: "Two pointers", exact: true }),
  ).toBeVisible();
  await s.getByRole("button", { name: "Mark complete" }).click();
  await expect(s.getByText("66% complete")).toBeVisible();

  await s.getByRole("link", { name: "Next →" }).click();
  await expect(s.getByRole("button", { name: "Mark complete" })).toBeDisabled();
  const popup = student.context.waitForEvent("page");
  await s.getByRole("button", { name: "Open the PDF" }).click();
  const tab = await popup;
  await tab.waitForRequest(/X-Amz-Signature/);
  await tab.close();
  await s.getByRole("button", { name: "Mark complete" }).click();
  await expect(s.getByText("Course completed", { exact: true })).toBeVisible();

  await s.getByText("Course outline", { exact: true }).first().click();
  for (const lesson of ["Watch: arrays", "Read: two pointers", "Handout"]) {
    await expect(
      s.getByRole("link", { name: new RegExp(`${lesson}.*\\(completed\\)`) }).first(),
    ).toBeVisible();
  }
  await s.goto("/learn");
  await expect(s.getByRole("link", { name: new RegExp(title) })).toContainText("100% complete");
  await student.context.close();

  // ---------------------------------------------------------------- 5. another batch can't see it
  const other = await newUser(browser, "ece.student@demo-college.local", "/learn", PHONE);
  await expect(other.page.getByRole("heading", { level: 1, name: "My learning" })).toBeVisible();
  await expect(other.page.getByRole("link", { name: new RegExp(title) })).toHaveCount(0);
  expect((await other.page.goto(`/learn/courses/${courseId}`))?.status()).toBe(404);
  await other.context.close();
});
