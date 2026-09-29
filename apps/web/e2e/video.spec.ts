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
      return response.json();
    },
    { method, route, body, headers },
  );
}

// Other specs (admin-import) add batches on every run, so walk every page instead of the first.
async function findBatch(page: Page, name: string): Promise<{ id: string } | undefined> {
  let cursor: string | null = null;
  do {
    const query: string = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
    const result = await api(page, "GET", `/batches?limit=100${query}`);
    const match = result.items.find((item: { name: string }) => item.name === name);
    if (match) return match;
    cursor = result.next_cursor;
  } while (cursor);
  return undefined;
}

test("upload, signed playback and student resume on a real video", async ({ page, browser }) => {
  test.setTimeout(120_000);
  await signIn(page, "author@skillifyme.local", "/teach/videos");
  await page.getByLabel("Title", { exact: true }).fill(`Video ${Date.now()}`);
  await page.getByLabel("MP4 video").setInputFiles(path.resolve("../api/tests/fixtures/video.mp4"));
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/videos") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Upload video", exact: true }).click();
  const { video } = await (await created).json();
  await expect(page.getByText("Video ready", { exact: true })).toBeVisible({ timeout: 30_000 });
  const course = await api(page, "POST", "/courses", { title: `Video flow ${Date.now()}` });
  // Outline edits require If-Match with the revision the previous response returned.
  const courseModule = await api(
    page,
    "POST",
    `/courses/${course.id}/modules`,
    { title: "Module" },
    { "If-Match": String(course.revision) },
  );
  const lesson = await api(
    page,
    "POST",
    `/courses/${course.id}/modules/${courseModule.id}/lessons`,
    {
      title: "Video lesson",
      lesson_type: "video",
      content: { video_asset_id: video.id },
    },
    { "If-Match": String(courseModule.course_revision) },
  );
  await api(page, "POST", `/courses/${course.id}/versions`, { release_type: "major" });
  const admin = await browser.newPage();
  const student = await browser.newPage();
  const unrelated = await browser.newPage();
  try {
    await signIn(admin, "admin@demo-college.local", "/admin/batches");
    const org = await api(admin, "GET", "/organizations/current");
    const batch = await findBatch(admin, "CSE 2026");
    if (!batch) throw new Error("Seeded batch CSE 2026 not found in Demo College");
    await api(page, "POST", `/courses/${course.id}/assignments`, {
      organization_id: org.id,
      batch_ids: [batch.id],
    });
    await signIn(student, "cse.student@demo-college.local");
    let enrollmentId = "";
    await expect
      .poll(async () => {
        const result = await api(student, "GET", "/enrollments?limit=100");
        enrollmentId =
          result.items.find((item: { course_id: string }) => item.course_id === course.id)?.id ??
          "";
        return enrollmentId;
      })
      .not.toBe("");
    await student.setViewportSize({ width: 360, height: 780 });
    await student.goto(`/learn/enrollments/${enrollmentId}/video/${lesson.id}`);
    const player = student.getByLabel("Lesson video");
    await expect(player).toBeVisible();
    await player.evaluate(async (element: HTMLVideoElement) => {
      element.muted = true;
      await element.play();
    });
    await expect
      .poll(() => player.evaluate((element: HTMLVideoElement) => element.currentTime))
      .toBeGreaterThan(3);
    const heartbeat = student.waitForResponse((response) =>
      response.url().endsWith("/progress/heartbeat"),
    );
    await player.evaluate((element: HTMLVideoElement) => element.pause());
    expect((await heartbeat).status()).toBe(204);
    await student.reload();
    await expect
      .poll(() => player.evaluate((element: HTMLVideoElement) => element.currentTime))
      .toBeGreaterThan(2);
    await signIn(unrelated, "ece.student@demo-college.local");
    const denied = await unrelated.evaluate(
      async ({ enrollmentId, lessonId }) =>
        (await fetch(`/backend/api/v1/enrollments/${enrollmentId}/lessons/${lessonId}/playback`))
          .status,
      { enrollmentId, lessonId: lesson.id },
    );
    expect(denied).toBe(404);
  } finally {
    await Promise.all([admin.close(), student.close(), unrelated.close()]);
  }
});
