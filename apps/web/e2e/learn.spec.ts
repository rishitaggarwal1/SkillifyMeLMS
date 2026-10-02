import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { signIn } from "./helpers";

async function api(
  page: Page,
  method: string,
  route: string,
  body?: unknown,
  headers: Record<string, string> = {},
) {
  return page.evaluate(
    async ({ method, route, body, headers }) => {
      const response = await fetch(`/backend/api/v1${route}`, {
        method,
        headers: { "Content-Type": "application/json", ...headers },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (!response.ok)
        throw new Error(`${method} ${route}: ${response.status} ${await response.text()}`);
      return response.status === 204 ? null : response.json();
    },
    { method, route, body, headers },
  );
}

/** Upload the fixture PDF the way the instructor UI does (presigned POST, then confirm). */
async function uploadPdf(page: Page): Promise<string> {
  const created = await api(page, "POST", "/files", {
    kind: "pdf",
    file_name: "handout.pdf",
    content_type: "application/pdf",
  });
  const bytes = [...readFileSync(path.resolve("e2e/fixtures/handout.pdf"))];
  await page.evaluate(
    async ({ upload, bytes }) => {
      const form = new FormData();
      for (const [name, value] of Object.entries(upload.fields as Record<string, string>))
        form.append(name, value);
      form.append("file", new Blob([new Uint8Array(bytes)], { type: "application/pdf" }));
      const response = await fetch(upload.url, { method: "POST", body: form });
      if (!response.ok) throw new Error(`storage: ${response.status}`);
    },
    { upload: created.upload, bytes },
  );
  await api(page, "POST", `/files/${created.file.id}/confirm`);
  return created.file.id;
}

const NOTES = {
  type: "doc",
  content: [
    { type: "heading", attrs: { level: 2 }, content: [{ type: "text", text: "Two pointers" }] },
    {
      type: "codeBlock",
      attrs: { language: "python" },
      content: [{ type: "text", text: "def two_sum(a):\n    return a" }],
    },
  ],
};

test("a student completes a course on a small phone; another batch can't see it", async ({
  browser,
}) => {
  test.setTimeout(150_000);
  const title = `Student course ${test.info().project.name} ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

  // --- Setup through the API: the publisher builds, publishes and grants; the college admin
  // distributes to CSE 2026.
  const author = await browser.newPage();
  await signIn(author, "author@skillifyme.local", "/teach/courses");
  const course = await api(author, "POST", "/courses", { title });
  const mod = await api(
    author,
    "POST",
    `/courses/${course.id}/modules`,
    { title: "Basics" },
    {
      "If-Match": String(course.revision),
    },
  );
  const notes = await api(
    author,
    "POST",
    `/courses/${course.id}/modules/${mod.id}/lessons`,
    {
      title: "Read: two pointers",
      lesson_type: "notes",
      is_required: true,
      content: { doc: NOTES },
    },
    { "If-Match": String(mod.course_revision) },
  );
  const fileId = await uploadPdf(author);
  await api(
    author,
    "POST",
    `/courses/${course.id}/modules/${mod.id}/lessons`,
    { title: "Handout", lesson_type: "pdf", is_required: true, content: { file_id: fileId } },
    { "If-Match": String(notes.course_revision) },
  );
  await api(author, "POST", `/courses/${course.id}/versions`, { release_type: "major" });
  const college = await browser.newPage();
  await signIn(college, "admin@demo-college.local", "/admin/batches");
  const org = await api(college, "GET", "/organizations/current");
  await api(author, "POST", `/courses/${course.id}/assignments`, {
    organization_id: org.id,
    batch_ids: [],
  });
  const batches = await api(college, "GET", `/batches?name=${encodeURIComponent("CSE 2026")}`);
  await api(college, "POST", `/courses/${course.id}/assignments`, {
    batch_ids: [batches.items[0].id],
  });
  await Promise.all([author.close(), college.close()]);

  // --- The CSE student, on a 360px phone.
  const context = await browser.newContext({ viewport: { width: 360, height: 780 } });
  const student = await context.newPage();
  try {
    await signIn(student, "cse.student@demo-college.local", "/learn");
    const card = student.getByRole("link", { name: new RegExp(title) });
    await expect(card).toBeVisible({ timeout: 30_000 }); // enrollment fan-out runs in the worker
    await expect(card).toContainText("0% complete");
    await card.click();

    // Resumes at the first lesson; notes are the API's sanitized, highlighted HTML.
    await expect(
      student.getByRole("heading", { level: 2, name: "Read: two pointers" }),
    ).toBeVisible();
    const lessonArea = student.getByRole("article", { name: "Lesson" });
    await expect(
      lessonArea.getByRole("heading", { level: 2, name: "Two pointers", exact: true }),
    ).toBeVisible();
    await expect(lessonArea.locator("pre.highlight span.k").first()).toHaveText("def");
    expect(await student.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      360,
    );

    await student.getByRole("button", { name: "Mark complete" }).click();
    await expect(student.getByText("50% complete")).toBeVisible();

    // The outline drawer shows the tick.
    await student.getByText("Course outline", { exact: true }).first().click();
    await expect(
      student.getByRole("link", { name: /Read: two pointers.*\(completed\)/ }).first(),
    ).toBeVisible();

    // PDF: must be opened before it can be completed.
    await student.getByRole("link", { name: "Next →" }).click();
    await expect(student.getByRole("heading", { level: 2, name: "Handout" })).toBeVisible();
    await expect(student.getByRole("button", { name: "Mark complete" })).toBeDisabled();
    const popup = context.waitForEvent("page");
    await student.getByRole("button", { name: "Open the PDF" }).click();
    const pdfTab = await popup;
    // The tab requests the signed URL (headless Chromium then downloads it: no PDF viewer).
    const request = await pdfTab.waitForRequest(/X-Amz-Signature/);
    expect(request.url()).toContain("handout");
    await pdfTab.close();
    await expect(student.getByRole("button", { name: "Mark complete" })).toBeEnabled();
    await student.getByRole("button", { name: "Mark complete" }).click();
    await expect(student.getByText("Course completed", { exact: true })).toBeVisible();

    await student.getByRole("link", { name: "← My learning" }).click();
    await expect(student.getByRole("link", { name: new RegExp(title) })).toContainText("Completed");
  } finally {
    await context.close();
  }

  // --- A student of the same college in another batch never sees it.
  const other = await browser.newPage();
  try {
    await signIn(other, "ece.student@demo-college.local", "/learn");
    await expect(other.getByRole("heading", { level: 1, name: "My learning" })).toBeVisible();
    await expect(other.getByRole("link", { name: new RegExp(title) })).toHaveCount(0);
  } finally {
    await other.close();
  }
});
