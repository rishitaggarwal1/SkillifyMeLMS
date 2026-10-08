/** Step 6 authors and grades through the UI. Student attempts use the existing API;
 * the student assessment UI is deliberately reserved for Step 7. */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type Page } from "@playwright/test";
import type { AuthorQuestion } from "../src/lib/api/types";
import { signIn } from "./helpers";

async function user(browser: Browser, email: string, landing: string, width: number) {
  const context = await browser.newContext({ viewport: { width, height: 800 } });
  const page = await context.newPage();
  await signIn(page, email, landing);
  return { context, page };
}
async function call(page: Page, method: string, route: string, body?: unknown, revision?: number) {
  const response = await page.request.fetch("/backend/api/v1" + route, {
    method,
    data: body,
    headers: {
      Origin: new URL(page.url()).origin,
      ...(revision === undefined ? {} : { "If-Match": String(revision) }),
    },
  });
  expect(response.ok(), method + " " + route + ": " + (await response.text())).toBe(true);
  return response.status() === 204 ? null : response.json();
}
async function accessible(page: Page) {
  const result = await new AxeBuilder({ page }).analyze();
  expect(
    result.violations.filter((v) => v.impact === "serious" || v.impact === "critical"),
  ).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    page.viewportSize()!.width,
  );
}
async function enrollment(page: Page, courseId: string) {
  let cursor: string | null = null;
  do {
    const result = await call(
      page,
      "GET",
      "/enrollments?limit=100" + (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""),
    );
    const row = result.items.find((e: { course_id: string }) => e.course_id === courseId);
    if (row) return row.id as string;
    cursor = result.next_cursor;
  } while (cursor);
  return undefined;
}

test("author banks, quizzes, rubric assignments and grade frozen attempt history", async ({
  browser,
}, info) => {
  test.setTimeout(420_000);
  const width = info.project.name === "mobile-chrome" ? 360 : 1280;
  const tag = Date.now() + "-" + Math.random().toString(36).slice(2, 7);
  const bankName = "Assessment bank " + tag;
  const courseTitle = "Assessment authoring " + tag;
  const author = await user(browser, "author@skillifyme.local", "/teach/question-banks", width);
  const a = author.page;
  const contexts = [author.context];
  try {
    await a.getByRole("button", { name: "Create bank", exact: true }).first().click();
    const bankDialog = a.getByRole("dialog");
    await bankDialog.getByLabel("Bank name").fill(bankName);
    await bankDialog.getByRole("button", { name: "Create bank", exact: true }).click();
    await expect(a.getByRole("heading", { level: 1, name: bankName })).toBeVisible();
    const bankId = a.url().split("/").at(-1)!;
    const skill = await call(a, "POST", "/skills", {
      name: "Assessment skill " + tag,
      slug: "assessment_" + tag.replaceAll("-", "_"),
    });
    for (const type of ["mcq_single", "mcq_multi", "fill_blank"]) {
      await a.getByRole("button", { name: "Add question", exact: true }).first().click();
      const dialog = a.getByRole("dialog");
      await dialog.getByLabel("Question type").selectOption(type);
      await dialog.getByLabel("Question", { exact: true }).fill(type + " " + tag);
      if (type === "fill_blank") {
        await dialog.getByLabel("Accepted answers (one per line)").fill("Python\nPY");
      } else {
        await dialog.getByLabel("Option 1", { exact: true }).fill("Python");
        await dialog.getByLabel("Option 2", { exact: true }).fill("CSS");
        if (type === "mcq_multi") {
          await dialog.getByRole("button", { name: "Add option" }).click();
          await dialog.getByLabel("Option 3", { exact: true }).fill("Java");
          await dialog.getByLabel("Option 3 is correct").check();
        }
      }
      await dialog.getByLabel("Explanation (optional)").fill("Private explanation " + tag);
      await dialog.getByRole("button", { name: "Save question", exact: true }).click();
      await expect(dialog.getByRole("heading", { name: "Edit question" })).toBeVisible();
      const skills = dialog.getByRole("region", { name: "Question skills" });
      await skills.getByLabel("Skills", { exact: true }).fill("Assessment skill " + tag);
      await skills.getByRole("button", { name: "Add", exact: true }).click();
      await expect(skills.getByRole("button", { name: "Remove " + skill.name })).toBeVisible();
      await accessible(a);
      await dialog.getByRole("button", { name: "Done" }).click();
    }
    await a.getByLabel("Filter question type").selectOption("fill_blank");
    await expect(a.getByRole("list", { name: "Bank questions" }).getByRole("listitem")).toHaveCount(
      1,
    );
    await a.getByLabel("Filter question type").selectOption("");
    await expect(a.getByRole("list", { name: "Bank questions" }).getByRole("listitem")).toHaveCount(
      3,
    );
    await accessible(a);
    const questions: AuthorQuestion[] = (
      await call(a, "GET", "/question-banks/" + bankId + "/questions")
    ).items;

    await a.goto("/teach/courses");
    await a.getByRole("button", { name: "New course" }).click();
    await a.getByLabel("Title", { exact: true }).fill(courseTitle);
    await a.getByRole("button", { name: "Create course" }).click();
    await expect(a.getByRole("heading", { level: 1, name: courseTitle })).toBeVisible();
    const courseUrl = a.url();
    const courseId = courseUrl.split("/").at(-1)!;
    await a.getByLabel("New module").fill("Assessments");
    await a.getByRole("button", { name: "Add module" }).click();
    const add = a.getByRole("form", { name: "Add lesson to Assessments" });
    for (const [type, title] of [
      ["quiz", "Knowledge check"],
      ["assignment", "Rubric homework"],
    ]) {
      await add.getByPlaceholder("Lesson title").fill(title!);
      await add.getByLabel("Type").selectOption(type!);
      await add.getByRole("button", { name: "Add lesson" }).click();
      await expect(a.getByRole("link", { name: title!, exact: true })).toBeVisible();
    }
    await a.getByRole("link", { name: "Knowledge check", exact: true }).click();
    await a.waitForURL(/\/lessons\/[^/]+$/);
    const quizId = a.url().split("/").at(-1)!;
    await expect(a.getByRole("checkbox", { name: "Required for course completion" })).toBeChecked();
    const quiz = a.getByRole("form", { name: "Quiz details" });
    await quiz.getByLabel("Search question banks").fill(bankName);
    await quiz.getByLabel("Question bank", { exact: true }).selectOption({ label: bankName });
    for (const question of questions)
      await quiz.getByRole("checkbox", { name: new RegExp(question.prompt) }).check();
    for (const i of [1, 2, 3]) await quiz.getByLabel("Marks for question " + i).fill("2");
    await quiz.getByLabel("Pass marks").fill("4");
    await quiz.getByLabel("Time limit (seconds)").fill("600");
    await quiz.getByLabel("Attempts allowed").fill("2");
    await quiz.getByLabel("After submitting, students may see").selectOption("explanations");
    await quiz.getByLabel("Reveal correct answers").selectOption("after_attempts_exhausted");
    await quiz.getByRole("button", { name: "Save quiz" }).click();
    await expect(a.getByText("Quiz saved")).toBeVisible();
    await a.reload();
    await expect(quiz.getByText(questions[0]!.prompt).first()).toBeVisible();
    await quiz.getByLabel("Choose questions").selectOption("bank");
    await quiz.getByLabel("Number of questions to draw").fill("3");
    await quiz.getByLabel("Marks per question").fill("2");
    const bankSkills = quiz.getByLabel("Skills", { exact: true });
    await bankSkills.fill(skill.name);
    await quiz
      .getByRole("list", { name: "Skill search results" })
      .getByRole("button", { name: "Add", exact: true })
      .click();
    await quiz.getByRole("button", { name: "Save quiz" }).click();
    await expect(a.getByText("Quiz saved").last()).toBeVisible();
    await accessible(a);

    await a.goto(courseUrl);
    await a.getByRole("link", { name: "Rubric homework", exact: true }).click();
    await a.waitForURL(/\/lessons\/[^/]+$/);
    const assignmentId = a.url().split("/").at(-1)!;
    const assignmentUrl = a.url();
    const details = a.getByRole("form", { name: "Assignment details" });
    const due = new Intl.DateTimeFormat("sv-SE", {
      timeZone: "Asia/Kolkata",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .format(new Date(Date.now() - 3600000))
      .replace(" ", "T");
    await details.getByLabel("Due (IST, optional)").fill(due);
    await details.getByRole("checkbox", { name: "Grade with a rubric" }).check();
    for (const [i, label, marks] of [
      [1, "Correctness", "6"],
      [2, "Clarity", "4"],
    ] as const) {
      await details.getByRole("button", { name: "Add criterion" }).click();
      await details.getByLabel("Criterion " + i + " label").fill(label);
      await details.getByLabel("Criterion " + i + " maximum marks").fill(marks);
    }
    await details.getByLabel("Late submissions").selectOption("penalty");
    await details.getByLabel("Penalty percent per day").fill("10");
    await details.getByRole("button", { name: "Save details" }).click();
    await expect(a.getByText("Assignment saved")).toBeVisible();
    await a
      .getByRole("textbox", { name: "Instructions" })
      .fill("Original frozen instructions " + tag);
    await a.getByLabel("Upload image").setInputFiles({
      name: "diagram.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=",
        "base64",
      ),
    });
    await expect(a.getByRole("textbox", { name: "Instructions" }).locator("img")).toHaveCount(1);
    await a.getByRole("button", { name: "Save instructions" }).click();
    await expect(a.getByText("Instructions saved")).toBeVisible();
    await a.getByText("Saved instructions preview", { exact: true }).click();
    await expect(a.locator("details[open] img")).toHaveCount(1);
    await accessible(a);
    await a.goto(courseUrl);
    await a.getByRole("button", { name: "Publish…" }).click();
    await a.getByRole("dialog").getByRole("button", { name: "Publish", exact: true }).click();
    await expect(a.getByText("Published v1.0").first()).toBeVisible();

    // Structural quiz rules appear in the existing preview and disable minor releases.
    await a.goto(courseUrl + "/lessons/" + quizId);
    await quiz.getByLabel("Attempts allowed").fill("3");
    await quiz.getByRole("button", { name: "Save quiz" }).click();
    await expect(a.getByText("Quiz saved")).toBeVisible();
    await a.goto(courseUrl);
    await a.getByRole("button", { name: "Publish…" }).click();
    await expect(
      a.getByRole("dialog").getByText(/Quiz question set, types\/options/),
    ).toBeVisible();
    await expect(a.getByRole("dialog").getByRole("radio", { name: /^Minor/ })).toBeDisabled();
    await a.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    await a.goto(courseUrl + "/lessons/" + quizId);
    await quiz.getByLabel("Attempts allowed").fill("2");
    await quiz.getByRole("button", { name: "Save quiz" }).click();
    await expect(a.getByText("Quiz saved")).toBeVisible();

    const admin = await user(browser, "admin@demo-college.local", "/admin", width);
    const student = await user(browser, "cse.student@demo-college.local", "/learn", width);
    const grader = await user(browser, "instructor@demo-college.local", "/teach/grading", width);
    contexts.push(admin.context, student.context, grader.context);
    const org = (await call(admin.page, "GET", "/me")).active_organization_id;
    const batch = (await call(admin.page, "GET", "/batches?name=CSE%202026")).items[0];
    await call(a, "POST", "/courses/" + courseId + "/assignments", {
      organization_id: org,
      batch_ids: [],
    });
    await call(admin.page, "POST", "/courses/" + courseId + "/assignments", {
      batch_ids: [batch.id],
    });
    let eid: string | undefined;
    await expect
      .poll(
        async () => {
          eid = await enrollment(student.page, courseId);
          return eid;
        },
        { timeout: 30_000 },
      )
      .toBeTruthy();
    const submissionPath = "/enrollments/" + eid + "/lessons/" + assignmentId + "/submission";
    const first = await call(
      student.page,
      "PUT",
      submissionPath,
      { submission: { kind: "text", text: "First attempt work" } },
      0,
    );
    const sid = first.id;
    const g = grader.page;
    const review = "/teach/submissions/" + sid + "?from=grading";
    await g.goto(review);
    await g.getByLabel("Correctness (out of 6)").fill("6");
    await g.getByLabel("Clarity (out of 4)").fill("2");
    await expect(g.getByLabel("Grade preview")).toContainText("7.20 / 10");
    await accessible(g);
    // Replacement is permitted until grading. Leave this first attempt ungraded.

    // A minor correction reaches future submissions; the first attempt retains its rubric/instructions.
    await a.goto(assignmentUrl);
    await details.getByLabel("Criterion 1 label").fill("Correct output");
    await details.getByRole("button", { name: "Save details" }).click();
    await expect(a.getByText("Assignment saved")).toBeVisible();
    await a.getByRole("textbox", { name: "Instructions" }).fill("Corrected instructions " + tag);
    await a.getByRole("button", { name: "Save instructions" }).click();
    await expect(a.getByText("Instructions saved")).toBeVisible();
    await a.goto(courseUrl);
    await a.getByRole("button", { name: "Publish…" }).click();
    await expect(a.getByRole("dialog").getByRole("radio", { name: /^Minor/ })).toBeChecked();
    await a.getByRole("dialog").getByRole("button", { name: "Publish", exact: true }).click();
    await expect(a.getByText("Published v1.1").first()).toBeVisible();
    const own = await call(
      student.page,
      "GET",
      "/enrollments/" + eid + "/lessons/" + assignmentId + "/assignment",
    );
    await call(
      student.page,
      "PUT",
      submissionPath,
      { submission: { kind: "text", text: "Second attempt work" } },
      own.submission.revision,
    );
    await g.goto(review);
    await expect(g.getByRole("region", { name: "Pinned instructions" })).toContainText(
      "Corrected instructions " + tag,
    );
    await g
      .getByRole("region", { name: "Attempt history" })
      .getByRole("button", { name: /^Attempt 1/ })
      .click();
    await expect(g.getByRole("region", { name: "Pinned instructions" })).toContainText(
      "Original frozen instructions " + tag,
    );
    await expect(g.getByRole("region", { name: "Pinned instructions" }).locator("img")).toHaveCount(
      1,
    );
    await expect(g.getByRole("form", { name: "Grade", exact: true })).toHaveCount(0);
    await expect(g.getByRole("region", { name: "Pinned rubric" })).toContainText("Correctness");
    await expect(g.getByRole("region", { name: "Grade history" })).toContainText(
      "This attempt has not been graded.",
    );
    await accessible(g);
    await g.getByRole("button", { name: "View active attempt" }).click();
    await g.getByLabel("Correct output (out of 6)").fill("6");
    await g.getByLabel("Clarity (out of 4)").fill("2.25");
    await expect(g.getByLabel("Grade preview")).toContainText("7.42 / 10");
    await g.getByRole("button", { name: "Save grade" }).click();
    await expect(g).toHaveURL(/\/teach\/grading$/);
    await g.goto(review);
    await expect(g.getByLabel("Clarity (out of 4)")).toHaveValue("2.25");
    await g.getByLabel("Clarity (out of 4)").fill("3");
    await g.getByRole("button", { name: "Update grade" }).click();
    await expect(g).toHaveURL(/\/teach\/grading$/);
    await g.goto(review);
    await expect(
      g.getByRole("region", { name: "Grade history" }).getByRole("listitem"),
    ).toHaveCount(2);
    await expect(g.getByRole("region", { name: "Grade history" })).toContainText("8.1 / 10");
    await expect(g.getByRole("region", { name: "Grade history" })).toContainText("7.42 / 10");
    await accessible(g);
    const other = await user(browser, "ece.student@demo-college.local", "/learn", width);
    contexts.push(other.context);
    expect(await enrollment(other.page, courseId)).toBeUndefined();
    const hidden = await other.page.request.get(
      "/backend/api/v1/enrollments/" + eid + "/lessons/" + assignmentId + "/assignment",
    );
    expect(hidden.status()).toBe(404);
    expect(await hidden.json()).toMatchObject({ error: { code: "not_found" } });
    await a.goto("/teach/question-banks/" + bankId);
    const bankRows = a.getByRole("list", { name: "Bank questions" }).getByRole("listitem");
    await expect(bankRows).toHaveCount(3);
    await bankRows.first().getByRole("button", { name: "Archive question" }).click();
    await a.getByRole("dialog").getByRole("button", { name: "Archive question" }).click();
    await expect(bankRows).toHaveCount(2);
    await a.getByRole("button", { name: "Archive bank" }).click();
    await a.getByRole("dialog").getByRole("button", { name: "Archive bank" }).click();
    await expect(a).toHaveURL(/\/teach\/question-banks$/);
    await a.getByLabel("Search banks").fill(bankName);
    await expect(a.getByRole("list", { name: "Question banks" }).getByRole("listitem")).toHaveCount(
      0,
    );
  } finally {
    for (const context of contexts) await context.close();
  }
});
