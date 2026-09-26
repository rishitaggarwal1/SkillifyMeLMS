import { expect, test } from "@playwright/test";

import { signIn } from "./helpers";

const STUDENTS = 50;

function studentsCsv(tag: string): Buffer {
  const rows = Array.from(
    { length: STUDENTS },
    (_, i) =>
      `student${String(i + 1).padStart(2, "0")}.${tag}@demo-college.local,Student ${i + 1} ${tag}`,
  );
  return Buffer.from(["email,full_name", ...rows].join("\n") + "\n");
}

test("an org admin creates a batch and imports 50 students into it", async ({ page }) => {
  // Real Keycloak accounts + setup emails for 50 people, processed by the background worker.
  test.setTimeout(180_000);
  const tag = `${Date.now().toString(36)}${test.info().project.name.startsWith("mobile") ? "m" : "d"}`;
  const batchName = `E2E ${tag}`;

  await signIn(page, "admin@demo-college.local", "/admin/batches");

  // 1. Create the batch.
  await page.getByRole("button", { name: "New batch" }).click();
  await page.getByLabel("Name").fill(batchName);
  await page.getByRole("button", { name: "Create batch" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  const batchLink = page.getByRole("link", { name: new RegExp(batchName) });
  await expect(batchLink).toContainText("0 members");

  // 2. From the batch, start a CSV import (the batch is preselected).
  await batchLink.click();
  await expect(page.getByRole("heading", { level: 1, name: batchName })).toBeVisible();
  await page.getByRole("link", { name: "Import CSV" }).click();
  await expect(page.getByLabel("Add to batch")).toHaveValue(/.+/);
  await expect(page.getByLabel("Add to batch").locator("option:checked")).toHaveText(batchName);
  await page.getByLabel("CSV file").setInputFiles({
    name: `students-${tag}.csv`,
    mimeType: "text/csv",
    buffer: studentsCsv(tag),
  });
  await page.getByRole("button", { name: "Start import" }).click();

  // 3. Watch the job finish.
  const job = page.getByTestId("import-job");
  await expect(job.getByTestId("import-status")).toHaveText("Done", { timeout: 150_000 });
  await expect(job.getByTestId("import-processed")).toHaveText(`${STUDENTS}/${STUDENTS}`);
  await expect(job.getByTestId("import-created")).toHaveText(String(STUDENTS));
  await expect(job.getByTestId("import-errors")).toHaveText("0");

  // 4. The batch now has the 50 students.
  await page.goto("/admin/batches");
  await expect(page.getByRole("link", { name: new RegExp(batchName) })).toContainText(
    `${STUDENTS} members`,
  );
  await page.goto("/admin/members");
  await page.getByLabel("Search members").fill(tag);
  await expect(page.getByRole("list", { name: "Members" }).getByRole("listitem")).toHaveCount(25); // first page
  await expect(page.getByRole("button", { name: "Load more" })).toBeVisible();
});
