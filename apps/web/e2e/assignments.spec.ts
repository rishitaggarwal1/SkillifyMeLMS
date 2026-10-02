/**
 * Phase 2.5 assignments, through the UI: the publisher defines and publishes an assignment, the
 * college admin assigns it to CSE 2026, the student submits on a 360px phone, the college's
 * instructor grades it (also on a phone), and the student sees the grade, the feedback and 100%.
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

test("an instructor grades a submission and the student sees the grade and 100%", async ({
  browser,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chrome", "one run is enough (it sets viewports)");
  test.setTimeout(240_000);
  const tag = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const title = `Assignments ${tag}`;

  // ---------------------------------------------------------------- 1. define and publish
  const author = await newUser(browser, "author@skillifyme.local", "/teach/courses");
  const a = author.page;
  await a.getByRole("button", { name: "New course" }).click();
  await a.getByLabel("Title").fill(title);
  await a.getByRole("button", { name: "Create course" }).click();
  await expect(a.getByRole("heading", { level: 1, name: title })).toBeVisible();
  const courseUrl = a.url();
  const courseId = courseUrl.split("/").at(-1)!;
  await a.getByLabel("New module").fill("Week 1");
  await a.getByRole("button", { name: "Add module" }).click();
  const addLesson = a.getByRole("form", { name: "Add lesson to Week 1" });
  await addLesson.getByPlaceholder("Lesson title").fill("FizzBuzz");
  await addLesson.getByLabel("Type").selectOption("assignment");
  await addLesson.getByRole("button", { name: "Add lesson" }).click();

  await a.getByRole("link", { name: "FizzBuzz" }).click();
  const details = a.getByRole("form", { name: "Assignment details" });
  await expect(details.getByLabel("Title students see")).toHaveValue("FizzBuzz");
  await details.getByLabel("Maximum marks").fill("10");
  await details.getByRole("button", { name: "Save details" }).click();
  await expect(a.getByText("Assignment saved")).toBeVisible();
  const instructions = a.getByRole("textbox", { name: "Instructions" });
  await instructions.click();
  await instructions.pressSequentially("Print the numbers 1 to 100, with Fizz and Buzz.");
  await a.getByRole("button", { name: "Save instructions" }).click();
  await expect(a.getByText("Instructions saved")).toBeVisible();

  await a.goto(courseUrl);
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

  // ---------------------------------------------------------------- 2. the college assigns it
  const admin = await newUser(browser, "admin@demo-college.local", `/teach/courses/${courseId}`);
  const d = admin.page;
  const cse = d.getByRole("checkbox", { name: /^CSE 2026/ });
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

  // ---------------------------------------------------------------- 3. the student submits
  const student = await newUser(browser, "cse.student@demo-college.local", "/learn", PHONE);
  const s = student.page;
  const card = s.getByRole("link", { name: new RegExp(title) });
  await expect(card).toBeVisible({ timeout: 30_000 }); // enrollment fan-out runs in the worker
  await card.click();
  await expect(s.getByRole("heading", { level: 2, name: "FizzBuzz" })).toBeVisible();
  await expect(s.getByText("Print the numbers 1 to 100")).toBeVisible();
  await expect(s.getByText("Out of 10 marks")).toBeVisible();
  await s.getByLabel("Your answer", { exact: true }).fill("for i in range(1, 101):\n    print(i)");
  await s.getByRole("button", { name: "Submit", exact: true }).click();
  const yours = s.getByRole("region", { name: "Your submission" });
  await expect(yours.getByText("Submitted", { exact: true })).toBeVisible();
  await expect(s.getByRole("button", { name: "Replace submission" })).toBeVisible();
  await noSideScroll(s);
  const lessonUrl = s.url();

  // ---------------------------------------------------------------- 4. the instructor grades
  const grader = await newUser(
    browser,
    "instructor@demo-college.local",
    `/teach/courses/${courseId}`,
    PHONE,
  );
  const g = grader.page;
  await g
    .getByRole("list", { name: "Assignments to grade" })
    .getByRole("link", { name: /FizzBuzz/ })
    .click();
  const rows = g.getByRole("list", { name: "Submissions" }).getByRole("listitem");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("To grade");
  await noSideScroll(g);
  await rows.first().getByRole("link").click();
  await expect(g.getByText("for i in range(1, 101):")).toBeVisible();
  const form = g.getByRole("form", { name: "Grade" });
  await form.getByLabel("Score (out of 10)").fill("12");
  await form.getByRole("button", { name: "Save grade" }).click();
  await expect(form.getByText("At most 10.")).toBeVisible(); // checked before the API
  await form.getByLabel("Score (out of 10)").fill("8.5");
  await form.getByLabel(/Feedback/).fill("Nice loop. Now add Fizz and Buzz.");
  await noSideScroll(g);
  await form.getByRole("button", { name: "Save grade" }).click();
  await expect(g.getByText("Nothing to grade.")).toBeVisible(); // back on the queue
  await grader.context.close();

  // ---------------------------------------------------------------- 5. the student sees it
  await s.goto(lessonUrl);
  const result = s.getByRole("region", { name: "Your grade" });
  await expect(result).toContainText("8.5 / 10");
  await expect(result).toContainText("Nice loop. Now add Fizz and Buzz.");
  await expect(result.getByText("✓ Completed")).toBeVisible();
  await expect(s.getByText("Course completed", { exact: true })).toBeVisible();
  await noSideScroll(s);
  await s.goto("/learn");
  await expect(s.getByRole("link", { name: new RegExp(title) })).toContainText("100% complete");
  await student.context.close();
});
