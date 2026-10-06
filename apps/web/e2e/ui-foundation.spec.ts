/** Step 5: shell visuals, accessibility of every page route, and approved landing navigation. */
import fs from "node:fs";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type Page } from "@playwright/test";

import { signIn } from "./helpers";

const ROUTES = [
  "/",
  "/catalog",
  "/catalog/[slug]",
  "/platform",
  "/platform/organizations",
  "/platform/organizations/[organizationId]",
  "/platform/users",
  "/platform/users/[userId]",
  "/platform/courses",
  "/platform/audit",
  "/admin",
  "/admin/batches",
  "/admin/batches/[batchId]",
  "/admin/courses",
  "/admin/members",
  "/admin/imports",
  "/teach",
  "/teach/courses",
  "/teach/courses/[courseId]",
  "/teach/courses/[courseId]/lessons/[lessonId]",
  "/teach/courses/[courseId]/assignments/[lessonId]",
  "/teach/courses/[courseId]/progress",
  "/teach/submissions/[submissionId]",
  "/teach/grading",
  "/teach/question-banks",
  "/teach/videos",
  "/learn",
  "/learn/enrollments/[enrollmentId]",
  "/learn/enrollments/[enrollmentId]/lessons/[lessonId]",
  "/learn/enrollments/[enrollmentId]/video/[lessonId]",
];

test("the accessibility inventory names every renderable application page route", () => {
  const dir = path.resolve("src/app");
  const pages = fs
    .readdirSync(dir, { recursive: true })
    .filter((f) => String(f).endsWith("page.tsx"))
    .map(
      (f) =>
        "/" +
        String(f)
          .replace(/\\/g, "/")
          .replace(/(?:^|\/)page\.tsx$/, ""),
    )
    .sort();
  expect([...ROUTES].sort()).toEqual(pages);
});

async function ready(page: Page) {
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("status", { name: "Loading page" })).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
}

async function scan(page: Page, route: string) {
  await page.goto(route);
  await ready(page);
  const result = await new AxeBuilder({ page }).analyze();
  const serious = result.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(
    serious.map((v) => ({
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.map((n) => ({ target: n.target, summary: n.failureSummary })),
    })),
    route,
  ).toEqual([]);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
    route,
  ).toBeLessThanOrEqual(page.viewportSize()!.width);
}

async function shellScreenshot(page: Page, area: string, width: number, theme: "light" | "dark") {
  await page.setViewportSize({ width, height: 800 });
  await page.getByRole("button", { name: "Appearance" }).click();
  await page
    .getByRole("menuitem", { name: theme === "light" ? "Light theme" : "Dark theme" })
    .click();
  await expect(page.locator("html")).toHaveClass(theme === "dark" ? /\bdark\b/ : /\blight\b/);
  await ready(page);
  await expect(page).toHaveScreenshot(`${area}-${width}-${theme}.png`, {
    animations: "disabled",
    scale: "css",
    fullPage: false,
    maxDiffPixelRatio: 0.015,
    // Assert header + sidebar/bottom nav; dynamic counts and course contents are outside this smoke contract.
    stylePath: path.resolve("e2e/shell-screenshot.css"),
  });
}

async function appCall(
  page: Page,
  method: string,
  route: string,
  body?: unknown,
  revision?: number,
) {
  const response = await page.request.fetch(`/backend/api/v1${route}`, {
    method,
    data: body,
    headers: {
      Origin: new URL(page.url()).origin,
      ...(revision === undefined ? {} : { "If-Match": String(revision) }),
    },
  });
  expect(response.ok(), `${method} ${route}: ${await response.text()}`).toBe(true);
  return response.status() === 204 ? null : response.json();
}

async function allEnrollments(
  page: Page,
): Promise<{ id: string; course_id: string; course_title: string }[]> {
  const rows: { id: string; course_id: string; course_title: string }[] = [];
  let cursor: string | null = null;
  do {
    const data = await appCall(
      page,
      "GET",
      `/enrollments?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    );
    rows.push(...data.items);
    cursor = data.next_cursor;
  } while (cursor);
  return rows;
}

async function user(browser: Browser, email: string, landing: string) {
  const context = await browser.newContext({
    viewport: { width: 360, height: 800 },
    colorScheme: "light",
  });
  const page = await context.newPage();
  await signIn(page, email, landing);
  await ready(page);
  return { context, page };
}

const roles = [
  {
    area: "platform",
    email: "platform.admin@skillifyme.local",
    routes: [
      "/platform",
      "/platform/organizations",
      "/platform/users",
      "/platform/courses",
      "/platform/audit",
    ],
  },
  {
    area: "admin",
    email: "admin@demo-college.local",
    routes: ["/admin", "/admin/batches", "/admin/courses", "/admin/members", "/admin/imports"],
  },
  {
    area: "teach",
    email: "instructor@demo-college.local",
    routes: [
      "/teach",
      "/teach/courses",
      "/teach/grading",
      "/teach/question-banks",
      "/teach/videos",
    ],
  },
  { area: "learn", email: "cse.student@demo-college.local", routes: ["/learn"] },
];

for (const role of roles) {
  test(`${role.area} shell at 360px and 1280px, both themes, accessible routes`, async ({
    page,
  }) => {
    test.setTimeout(180_000);
    await signIn(page, role.email, `/${role.area}`);
    await ready(page);
    await expect(page.getByTestId("active-role")).toBeVisible();
    for (const width of [360, 1280]) {
      for (const theme of ["light", "dark"] as const) {
        await page.goto(`/${role.area}`);
        await ready(page);
        await shellScreenshot(page, role.area, width, theme);
        for (const route of role.routes) await scan(page, route);
      }
    }
    await page.goto(`/${role.area}`);
    await ready(page);
    const skip = page.getByRole("link", { name: "Skip to content" });
    await skip.focus();
    await expect(skip).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "SkillifyMe", exact: true })).toBeFocused();
    expect(await page.evaluate(() => getComputedStyle(document.activeElement!).outlineWidth)).toBe(
      "2px",
    );
    await page.setViewportSize({ width: 360, height: 800 });
    for (const item of await page.getByTestId("mobile-navigation").locator("a,button").all()) {
      const box = await item.boundingBox();
      expect(box!.width).toBeGreaterThanOrEqual(44);
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }
    await page.emulateMedia({ reducedMotion: "reduce" });
    const duration = await page
      .getByRole("button", { name: "Appearance" })
      .evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(Math.max(...duration.split(",").map(Number.parseFloat))).toBeLessThanOrEqual(0.00001);
  });
}

test("public, chooser and authentication destination states are accessible and theme follows the system", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.emulateMedia({ colorScheme: "dark" });
  await scan(page, "/");
  await expect(page.locator("html")).toHaveClass(/\bdark\b/);
  await page.getByRole("button", { name: "Appearance" }).click();
  await page.getByRole("menuitem", { name: "Light theme" }).click();
  await page.reload();
  await ready(page);
  await expect(page.locator("html")).toHaveClass(/\blight\b/);
  await page.getByRole("button", { name: "Appearance" }).click();
  await page.getByRole("menuitem", { name: "Use system setting" }).click();
  await expect(page.locator("html")).toHaveClass(/\bdark\b/);
  await scan(page, "/catalog");
  const catalog = await page.request.get("/backend/api/v1/catalog?limit=1");
  const entries = await catalog.json();
  expect(entries.items.length).toBeGreaterThan(0);
  await scan(page, `/catalog/${entries.items[0].slug}`);
  await scan(page, "/auth/callback?error=access_denied");
  await signIn(page, "multi@skillifyme.local", "/");
  await expect(
    page.getByRole("heading", { level: 1, name: "Where do you want to work?" }),
  ).toBeVisible();
  await ready(page);
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter((v) => v.impact === "serious" || v.impact === "critical"),
  ).toEqual([]);
});

test("detail/editor/player routes and cross-course grading share accessible components", async ({
  browser,
}) => {
  test.setTimeout(240_000);
  const tag = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const author = await user(browser, "author@skillifyme.local", "/teach/courses");
  const admin = await user(browser, "admin@demo-college.local", "/admin");
  const student = await user(browser, "cse.student@demo-college.local", "/learn");
  const grader = await user(browser, "instructor@demo-college.local", "/teach");
  const platform = await user(browser, "platform.admin@skillifyme.local", "/platform");
  try {
    const batches = await appCall(admin.page, "GET", "/batches?name=CSE%202026");
    expect(batches.items).toHaveLength(1);
    const batchId = batches.items[0].id;
    const college = (await appCall(admin.page, "GET", "/me")).active_organization_id;
    const course = await appCall(author.page, "POST", "/courses", {
      title: `UI foundation ${tag}`,
    });
    let revision = course.revision;
    const moduleRow = await appCall(
      author.page,
      "POST",
      `/courses/${course.id}/modules`,
      { title: "Week 1" },
      revision++,
    );
    const notes = await appCall(
      author.page,
      "POST",
      `/courses/${course.id}/modules/${moduleRow.id}/lessons`,
      {
        title: "Notes",
        lesson_type: "notes",
        content: {
          doc: {
            type: "doc",
            content: [{ type: "paragraph", content: [{ type: "text", text: "Practice Python" }] }],
          },
        },
      },
      revision++,
    );
    const assignment = await appCall(
      author.page,
      "POST",
      `/courses/${course.id}/modules/${moduleRow.id}/lessons`,
      { title: `Homework ${tag}`, lesson_type: "assignment", content: {} },
      revision++,
    );
    await appCall(
      author.page,
      "POST",
      `/courses/${course.id}/modules/${moduleRow.id}/lessons`,
      { title: "Practice lab", lesson_type: "lab", is_required: false, content: {} },
      revision++,
    );
    await appCall(
      author.page,
      "PUT",
      `/courses/${course.id}/lessons/${assignment.id}/assignment`,
      {
        title: `Homework ${tag}`,
        max_marks: 10,
        submission_kinds: ["text"],
        due_at: new Date(Date.now() + 86400000).toISOString(),
      },
      revision++,
    );
    await appCall(
      author.page,
      "POST",
      `/courses/${course.id}/versions`,
      { release_type: "major" },
      revision,
    );
    await appCall(author.page, "POST", `/courses/${course.id}/assignments`, {
      organization_id: college,
      batch_ids: [],
    });
    await appCall(admin.page, "POST", `/courses/${course.id}/assignments`, {
      batch_ids: [batchId],
    });
    let enrollmentId: string | undefined;
    await expect
      .poll(
        async () => {
          const enrolled = await allEnrollments(student.page);
          enrollmentId = enrolled.find((e: { course_id: string }) => e.course_id === course.id)?.id;
          return enrollmentId;
        },
        { timeout: 30_000 },
      )
      .toBeTruthy();
    await appCall(
      student.page,
      "PUT",
      `/enrollments/${enrollmentId}/lessons/${assignment.id}/submission`,
      { submission: { kind: "text", text: "print('hello')" } },
      0,
    );
    const own = await appCall(
      student.page,
      "GET",
      `/enrollments/${enrollmentId}/lessons/${assignment.id}/assignment`,
    );
    const submissionId = own.submission.id;
    const organizations = await appCall(platform.page, "GET", "/organizations?limit=1");
    const users = await appCall(platform.page, "GET", "/platform/users?limit=1");
    const targetOrg = organizations.items[0].id;
    const targetUser = users.items[0].id;
    const detailRoutes: [Page, string[]][] = [
      [platform.page, [`/platform/organizations/${targetOrg}`, `/platform/users/${targetUser}`]],
      [admin.page, [`/admin/batches/${batchId}`]],
      [
        author.page,
        [
          `/teach/courses/${course.id}`,
          `/teach/courses/${course.id}/lessons/${notes.id}`,
          `/teach/courses/${course.id}/lessons/${assignment.id}`,
        ],
      ],
      [
        grader.page,
        [
          `/teach/courses/${course.id}/assignments/${assignment.id}`,
          `/teach/submissions/${submissionId}`,
          `/teach/courses/${course.id}/progress?batch=${batchId}`,
        ],
      ],
      [
        student.page,
        [
          `/learn/enrollments/${enrollmentId}`,
          `/learn/enrollments/${enrollmentId}/lessons/${notes.id}`,
          `/learn/enrollments/${enrollmentId}/lessons/${assignment.id}`,
        ],
      ],
    ];
    // Existing demo provides a ready local video without an artificial readiness bypass.
    const enrollments = await allEnrollments(student.page);
    const demo = enrollments.find(
      (e: { course_title: string }) => e.course_title === "Python Foundations",
    );
    if (!demo) throw new Error("Python Foundations demo enrollment is missing");
    const detail = await appCall(student.page, "GET", `/enrollments/${demo.id}`);
    const video = detail.outline.modules
      .flatMap((m: { lessons: { id: string; lesson_type: string }[] }) => m.lessons)
      .find((l: { lesson_type: string }) => l.lesson_type === "video");
    expect(video).toBeTruthy();
    detailRoutes.push([student.page, [`/learn/enrollments/${demo.id}/video/${video.id}`]]);
    for (const width of [360, 1280]) {
      for (const [page, routes] of detailRoutes) {
        await page.setViewportSize({ width, height: 800 });
        for (const theme of ["light", "dark"] as const) {
          await page.getByRole("button", { name: "Appearance" }).click();
          await page
            .getByRole("menuitem", { name: theme === "light" ? "Light theme" : "Dark theme" })
            .click();
          for (const route of routes) await scan(page, route);
        }
      }
    }
    await grader.page.setViewportSize({ width: 360, height: 800 });
    await grader.page.goto("/teach/grading");
    await ready(grader.page);
    const target = grader.page
      .getByRole("list", { name: "Submissions" })
      .getByRole("link")
      .filter({ hasText: `Homework ${tag}` });
    while ((await target.count()) === 0) {
      const before = await grader.page
        .getByRole("list", { name: "Submissions" })
        .getByRole("listitem")
        .count();
      await grader.page.getByRole("button", { name: "Load more" }).click();
      await expect
        .poll(() =>
          grader.page.getByRole("list", { name: "Submissions" }).getByRole("listitem").count(),
        )
        .toBeGreaterThan(before);
    }
    await target.click();
    await grader.page.getByLabel("Score (out of 10)").fill("8");
    await grader.page.getByRole("button", { name: "Save grade" }).click();
    await expect(grader.page).toHaveURL(/\/teach\/grading$/);
    await student.page.goto("/learn");
    await ready(student.page);
    await expect(
      student.page.getByRole("region", { name: "Recent results" }).getByText(`Homework ${tag}`),
    ).toBeVisible();
  } finally {
    for (const u of [author, admin, student, grader, platform]) await u.context.close();
  }
});
