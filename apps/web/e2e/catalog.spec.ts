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
      const course = /^\/courses\/([^/]+)\/versions$/.exec(route);
      if (method === "POST" && course && !("If-Match" in headers)) {
        const current = await fetch(`/backend/api/v1/courses/${course[1]}`);
        if (!current.ok) throw new Error(`Course revision: ${current.status}`);
        headers["If-Match"] = String((await current.json()).revision);
      }
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

test("a published public course appears in the catalog for signed-out visitors", async ({
  page,
  browser,
  request,
}) => {
  test.setTimeout(90_000); // the catalog page may need a revalidation round trip (CI: production)
  const title = `Catalog course ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  await signIn(page, "author@skillifyme.local", "/teach/videos");
  const course = await api(page, "POST", "/courses", {
    title,
    description: "Arrays and two pointers, step by step.",
    is_public_catalog: true,
  });
  const courseModule = await api(
    page,
    "POST",
    `/courses/${course.id}/modules`,
    { title: "Basics" },
    { "If-Match": String(course.revision) },
  );
  await api(
    page,
    "POST",
    `/courses/${course.id}/modules/${courseModule.id}/lessons`,
    { title: "Notes", lesson_type: "notes", content: { doc: { type: "doc", content: [] } } },
    { "If-Match": String(courseModule.course_revision) },
  );
  await api(page, "POST", `/courses/${course.id}/versions`, { release_type: "major" });

  // A signed-out visitor on a small phone.
  const visitor = await browser.newContext({ viewport: { width: 360, height: 780 } });
  try {
    const catalog = await visitor.newPage();
    // Production (CI) serves the statically generated catalog: the new course appears once the
    // worker's publish-time revalidation has reached the web app, so reload until it does.
    const card = catalog.getByRole("list", { name: "Courses" }).getByRole("link", { name: title });
    await expect(async () => {
      await catalog.goto("/catalog");
      await expect(card).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 45_000 });
    await expect(catalog.getByRole("heading", { level: 1, name: "Course catalog" })).toBeVisible();
    await card.click();
    await expect(catalog.getByRole("heading", { level: 1, name: title })).toBeVisible();
    await expect(catalog.getByText("1 lesson")).toBeVisible();
    const width = await catalog.evaluate(() => document.documentElement.scrollWidth);
    expect(width).toBeLessThanOrEqual(360); // no horizontal scrolling on phones
  } finally {
    await visitor.close();
  }

  // The revalidation endpoint refuses callers without the shared secret.
  const refused = await request.post("/api/revalidate", { data: { tags: ["catalog"] } });
  expect(refused.status()).toBe(401);
});
