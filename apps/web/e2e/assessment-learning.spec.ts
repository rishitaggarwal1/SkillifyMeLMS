import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
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
async function enrollment(page: Page, courseId: string): Promise<string | undefined> {
  let cursor: string | null = null;
  do {
    const result = await call(
      page,
      "GET",
      "/enrollments?limit=100" + (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""),
    );
    const row = result.items.find((e: { course_id: string }) => e.course_id === courseId);
    if (row) return row.id;
    cursor = result.next_cursor;
  } while (cursor);
}
async function assign(author: Page, admin: Page, student: Page, courseId: string) {
  const org = (await call(admin, "GET", "/me")).active_organization_id;
  const batch = (await call(admin, "GET", "/batches?name=CSE%202026")).items[0];
  await call(author, "POST", "/courses/" + courseId + "/assignments", {
    organization_id: org,
    batch_ids: [],
  });
  await call(admin, "POST", "/courses/" + courseId + "/assignments", { batch_ids: [batch.id] });
  let id: string | undefined;
  await expect
    .poll(
      async () => {
        id = await enrollment(student, courseId);
        return id;
      },
      { timeout: 30_000 },
    )
    .toBeTruthy();
  return id!;
}
async function submit(page: Page) {
  await page.getByRole("button", { name: "Submit quiz", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Submit attempt", exact: true })
    .click();
  await expect(page.getByRole("region", { name: "Quiz result", exact: true })).toBeVisible();
}

test("student resumes checkpoints, fails then passes, submits late and sees rubric grades and 100% progress", async ({
  browser,
}, info) => {
  test.setTimeout(420_000);
  const width = info.project.name === "mobile-chrome" ? 360 : 1280;
  const tag = Date.now() + "-" + Math.random().toString(36).slice(2, 7);
  const bankName = "Learning bank " + tag;
  const title = "Assessment learning " + tag;
  const author = await user(browser, "author@skillifyme.local", "/teach/question-banks", width);
  const contexts: BrowserContext[] = [author.context];
  const a = author.page;
  try {
    // Build through the instructor UI, including all three question types and a rubric.
    await a.getByRole("button", { name: "Create bank", exact: true }).first().click();
    await a.getByRole("dialog").getByLabel("Bank name").fill(bankName);
    await a.getByRole("dialog").getByRole("button", { name: "Create bank", exact: true }).click();
    await expect(a.getByRole("heading", { level: 1, name: bankName })).toBeVisible();
    const bankId = a.url().split("/").at(-1)!;
    for (const type of ["mcq_single", "mcq_multi", "fill_blank"]) {
      await a.getByRole("button", { name: "Add question", exact: true }).first().click();
      const dialog = a.getByRole("dialog");
      await dialog.getByLabel("Question type").selectOption(type);
      await dialog.getByLabel("Question", { exact: true }).fill(type + " " + tag);
      if (type === "fill_blank")
        await dialog.getByLabel("Accepted answers (one per line)").fill("Python");
      else {
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
      await dialog.getByRole("button", { name: "Done" }).click();
    }
    const questions: { id: string; prompt: string }[] = (
      await call(a, "GET", "/question-banks/" + bankId + "/questions")
    ).items;
    await a.goto("/teach/courses");
    await a.getByRole("button", { name: "New course" }).click();
    await a.getByLabel("Title", { exact: true }).fill(title);
    await a.getByRole("button", { name: "Create course" }).click();
    await expect(a.getByRole("heading", { level: 1, name: title })).toBeVisible();
    const courseUrl = a.url();
    const courseId = courseUrl.split("/").at(-1)!;
    await a.getByLabel("New module").fill("Assessments");
    await a.getByRole("button", { name: "Add module" }).click();
    const add = a.getByRole("form", { name: "Add lesson to Assessments" });
    for (const [type, name] of [
      ["quiz", "Knowledge check"],
      ["assignment", "Rubric homework"],
    ]) {
      await add.getByPlaceholder("Lesson title").fill(name!);
      await add.getByLabel("Type").selectOption(type!);
      await add.getByRole("button", { name: "Add lesson" }).click();
      await expect(a.getByRole("link", { name: name!, exact: true })).toBeVisible();
    }
    await a.getByRole("link", { name: "Knowledge check", exact: true }).click();
    await a.waitForURL(/\/lessons\/[^/]+$/);
    const quizLesson = a.url().split("/").at(-1)!;
    const quiz = a.getByRole("form", { name: "Quiz details" });
    await quiz.getByLabel("Search question banks").fill(bankName);
    await quiz.getByLabel("Question bank", { exact: true }).selectOption({ label: bankName });
    for (const q of questions)
      await quiz.getByRole("checkbox", { name: new RegExp(q.prompt) }).check();
    for (const i of [1, 2, 3]) await quiz.getByLabel("Marks for question " + i).fill("2");
    await quiz.getByLabel("Pass marks").fill("5");
    await quiz.getByLabel("Time limit (seconds)").fill("600");
    await quiz.getByLabel("Attempts allowed").fill("2");
    await quiz.getByLabel("After submitting, students may see").selectOption("explanations");
    await quiz.getByLabel("Reveal correct answers").selectOption("after_attempts_exhausted");
    await quiz.getByRole("button", { name: "Save quiz" }).click();
    await expect(a.getByText("Quiz saved")).toBeVisible();
    await accessible(a);
    await a.goto(courseUrl);
    await a.getByRole("link", { name: "Rubric homework", exact: true }).click();
    await a.waitForURL(/\/lessons\/[^/]+$/);
    const assignmentLesson = a.url().split("/").at(-1)!;
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
    await a.getByRole("textbox", { name: "Instructions" }).fill("Original instructions " + tag);
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
    await a.goto(courseUrl);
    await a.getByRole("button", { name: "Publish…" }).click();
    await a.getByRole("dialog").getByRole("button", { name: "Publish", exact: true }).click();
    await expect(a.getByText("Published v1.0").first()).toBeVisible();

    const admin = await user(browser, "admin@demo-college.local", "/admin", width);
    const student = await user(browser, "cse.student@demo-college.local", "/learn", width);
    const grader = await user(browser, "instructor@demo-college.local", "/teach/grading", width);
    contexts.push(admin.context, student.context, grader.context);
    const eid = await assign(a, admin.page, student.page, courseId);
    const s = student.page;
    const quizUrl = "/learn/enrollments/" + eid + "/lessons/" + quizLesson;
    const assignmentPlayer = "/learn/enrollments/" + eid + "/lessons/" + assignmentLesson;
    await s.goto(quizUrl);
    const started = s.waitForResponse(
      (r) => r.url().endsWith("/quiz-attempts") && r.request().method() === "POST",
    );
    await s.getByRole("button", { name: "Start quiz", exact: true }).click();
    const first = await (await started).json();
    expect(JSON.stringify(first)).not.toContain("answer_key");
    expect(JSON.stringify(first)).not.toContain("Private explanation");
    const single = s.getByRole("group").filter({ hasText: "mcq_single " + tag });
    const multi = s.getByRole("group").filter({ hasText: "mcq_multi " + tag });
    const blank = s.getByRole("textbox", { name: /^Answer to question/ });
    let disconnected = true;
    await s.route("**/quiz-attempts/*/answers", async (route) => {
      if (disconnected) await route.abort("failed");
      else await route.continue();
    });
    await single.getByRole("radio", { name: "CSS", exact: true }).check();
    await multi.getByRole("checkbox", { name: "Python", exact: true }).check();
    await blank.fill("Rust");
    await expect(
      s.getByText("Your changes have not been saved. Check your connection and retry.", {
        exact: true,
      }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(blank).toHaveValue("Rust");
    await s.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const timerBounds = await s.getByRole("timer", { name: "Time remaining" }).boundingBox();
    const headerBounds = await s.getByTestId("app-header").boundingBox();
    expect(timerBounds).not.toBeNull();
    expect(headerBounds).not.toBeNull();
    expect(timerBounds!.y).toBeGreaterThanOrEqual(headerBounds!.y + headerBounds!.height);
    expect(timerBounds!.y + timerBounds!.height).toBeLessThanOrEqual(s.viewportSize()!.height);
    disconnected = false;
    await s.getByRole("alert").getByRole("button", { name: "Retry", exact: true }).click();
    await expect(s.getByText("All answers saved", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    const accepted = await call(s, "GET", "/quiz-attempts/" + first.id);
    expect(
      accepted.questions.find((q: { question_type: string }) => q.question_type === "fill_blank")
        .saved_answer.text,
    ).toBe("Rust");
    expect(accepted.expires_at).toBe(first.expires_at);
    await s.unroute("**/quiz-attempts/*/answers");
    await s.reload();
    await s.getByRole("button", { name: "Resume quiz", exact: true }).first().click();
    await expect(blank).toHaveValue("Rust");
    await expect(single.getByRole("radio", { name: "CSS", exact: true })).toBeChecked();
    await expect(multi.getByRole("checkbox", { name: "Python", exact: true })).toBeChecked();
    // Simulate a concurrent device using the ordinary own-attempt endpoint.
    const blankId = accepted.questions.find(
      (q: { question_type: string }) => q.question_type === "fill_blank",
    ).id;
    await call(
      s,
      "PUT",
      "/quiz-attempts/" + first.id + "/answers",
      { answers: [{ question_id: blankId, answer: { text: "Other device" } }] },
      accepted.revision,
    );
    await blank.fill("Local unsaved work");
    await expect(
      s.getByText(
        "This attempt changed on another device. Refresh to use its saved answers; your unsaved changes will be discarded.",
        { exact: true },
      ),
    ).toBeVisible({ timeout: 15_000 });
    await expect(blank).toHaveValue("Local unsaved work");
    await expect(blank).toBeDisabled();
    const concurrent = await call(s, "GET", "/quiz-attempts/" + first.id);
    expect(
      concurrent.questions.find((q: { id: string }) => q.id === blankId).saved_answer.text,
    ).toBe("Other device");
    await accessible(s);
    await s.getByRole("button", { name: "Refresh saved answers", exact: true }).click();
    await s
      .getByRole("dialog")
      .getByRole("button", { name: "Refresh saved answers", exact: true })
      .click();
    await expect(blank).toHaveValue("Other device");
    await accessible(s);
    await submit(s);
    const result = s.getByRole("region", { name: "Quiz result", exact: true });
    await expect(result).toContainText("Not passed");
    await expect(result).toContainText("1 / 6");
    await expect(s.getByRole("region", { name: "Correct answers", exact: true })).toHaveCount(0);
    await expect(s.getByText("Private explanation " + tag)).toHaveCount(0);
    await expect(s.getByRole("progressbar", { name: "Course progress" })).toHaveAttribute(
      "aria-valuenow",
      "0",
    );
    await accessible(s);
    await result.getByRole("button", { name: "Try again" }).click();
    await single.getByRole("radio", { name: "Python", exact: true }).check();
    await multi.getByRole("checkbox", { name: "Python", exact: true }).check();
    await multi.getByRole("checkbox", { name: "Java", exact: true }).check();
    await blank.fill("Python");
    await submit(s);
    await expect(result).toContainText("Passed");
    await expect(result).toContainText("6 / 6");
    await expect(s.getByRole("region", { name: "Correct answers", exact: true })).toBeVisible();
    await expect(s.getByText("Private explanation " + tag)).toHaveCount(3);
    await expect(s.getByRole("progressbar", { name: "Course progress" })).toHaveAttribute(
      "aria-valuenow",
      "50",
    );
    await accessible(s);

    await s.goto(assignmentPlayer);
    await expect(s.getByRole("region", { name: "Before submitting" })).toContainText("10% penalty");
    await expect(s.getByRole("article", { name: "Lesson" }).locator("img")).toHaveCount(1);
    await expect(s.getByRole("article", { name: "Lesson" }).locator("img")).toHaveJSProperty(
      "naturalWidth",
      1,
    );
    await s.getByRole("textbox", { name: "Your answer", exact: true }).fill("First late attempt");
    await s.getByRole("button", { name: "Submit", exact: true }).click();
    await expect(s.getByText("Submitted", { exact: true }).first()).toBeVisible();
    await accessible(s);
    // A minor correction affects future submissions; history retains the original image/rubric.
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
    await s.reload();
    await s.getByRole("textbox", { name: "Your answer", exact: true }).fill("Second late attempt");
    await s.getByRole("button", { name: "Replace submission" }).click();
    await expect(s.getByText("Submission replaced", { exact: true })).toBeVisible();
    await s.getByText("Submission and grade history", { exact: true }).click();
    const history = s.getByRole("region", { name: "Submission attempt history" });
    await history.getByRole("button", { name: /^Attempt 1/ }).click();
    await expect(history.getByRole("region", { name: "Pinned instructions" })).toContainText(
      "Original instructions " + tag,
    );
    await expect(
      history.getByRole("region", { name: "Pinned instructions" }).locator("img"),
    ).toHaveJSProperty("naturalWidth", 1);
    await expect(history.getByRole("region", { name: "Rubric" })).toContainText("Correctness");
    await expect(history.getByRole("region", { name: "Grade history" })).toContainText(
      "This attempt has not been graded.",
    );
    await accessible(s);
    const own = await call(
      s,
      "GET",
      "/enrollments/" + eid + "/lessons/" + assignmentLesson + "/assignment",
    );
    const review = "/teach/submissions/" + own.submission.id + "?from=grading";
    const g = grader.page;
    await g.goto(review);
    await g.getByLabel("Correct output (out of 6)").fill("6");
    await g.getByLabel("Clarity (out of 4)").fill("2");
    await expect(g.getByLabel("Grade preview")).toContainText("7.20 / 10");
    await g.getByRole("button", { name: "Save grade" }).click();
    await expect(g).toHaveURL(/\/teach\/grading$/);
    // The open player notices a grade without reloading.
    await expect(s.getByRole("region", { name: "Your grade" })).toBeVisible({ timeout: 25_000 });
    const score = s.getByRole("region", { name: "Your grade" });
    await expect(score).toContainText("Correct output");
    await expect(score).toContainText("Earned marks8 / 10");
    await expect(score).toContainText("Late penalty (10%)−0.8");
    await expect(score).toContainText("Final score7.2 / 10");
    await expect(s.getByRole("progressbar", { name: "Course progress" })).toHaveAttribute(
      "aria-valuenow",
      "100",
    );
    await expect(s.getByText("Course completed", { exact: true })).toBeVisible();
    await history.getByRole("button", { name: /^Attempt 2/ }).click();
    await expect(history.getByRole("region", { name: "Grade history" })).toContainText(
      "Final score7.2 / 10",
    );
    await accessible(s);
    const other = await user(browser, "ece.student@demo-college.local", "/learn", width);
    contexts.push(other.context);
    expect(await enrollment(other.page, courseId)).toBeUndefined();
    for (const route of [
      "/enrollments/" + eid + "/lessons/" + quizLesson + "/quiz",
      "/quiz-attempts/" + first.id + "/results",
      "/enrollments/" + eid + "/lessons/" + assignmentLesson + "/submission-attempts",
    ]) {
      const hidden = await other.page.request.get("/backend/api/v1" + route);
      expect(hidden.status()).toBe(404);
      expect(await hidden.json()).toMatchObject({ error: { code: "not_found" } });
    }
    await accessible(other.page);
  } finally {
    for (const context of contexts) await context.close();
  }
});

test("server expiry forces submission of accepted checkpoints with no late edits", async ({
  browser,
}, info) => {
  test.setTimeout(180_000);
  const width = info.project.name === "mobile-chrome" ? 360 : 1280;
  const author = await user(browser, "author@skillifyme.local", "/teach/courses", width);
  const admin = await user(browser, "admin@demo-college.local", "/admin", width);
  const student = await user(browser, "cse.student@demo-college.local", "/learn", width);
  try {
    // Ordinary author APIs prepare a legitimately short quiz; no test-only production hook.
    const a = author.page;
    const tag = Date.now() + "-" + Math.random().toString(36).slice(2, 7);
    const bank = await call(a, "POST", "/question-banks", { name: "Expiry " + tag }, 0);
    const question = await call(
      a,
      "POST",
      "/question-banks/" + bank.id + "/questions",
      {
        question_type: "fill_blank",
        prompt: "Language",
        options: [],
        answer_key: { accepted_answers: ["Python"], correct_option_ids: [], case_sensitive: false },
        explanation: "Private",
      },
      bank.revision,
    );
    const course = await call(a, "POST", "/courses", { title: "Timed learning " + tag });
    const mod = await call(
      a,
      "POST",
      "/courses/" + course.id + "/modules",
      { title: "Timed" },
      course.revision,
    );
    const lesson = await call(
      a,
      "POST",
      "/courses/" + course.id + "/modules/" + mod.id + "/lessons",
      { title: "Timed quiz", lesson_type: "quiz" },
      mod.course_revision,
    );
    const definition = await call(
      a,
      "PUT",
      "/courses/" + course.id + "/lessons/" + lesson.id + "/quiz",
      {
        title: "Timed quiz",
        time_limit_seconds: 25,
        attempts_allowed: 1,
        pass_marks: "1",
        randomize_order: false,
        selection: { mode: "manual", questions: [{ question_id: question.id, marks: "1" }] },
        reveal_mode: "score_only",
        reveal_timing: "immediately",
      },
      lesson.course_revision,
    );
    await call(
      a,
      "POST",
      "/courses/" + course.id + "/versions",
      { release_type: "major" },
      definition.course_revision,
    );
    const eid = await assign(a, admin.page, student.page, course.id);
    const s = student.page;
    await s.goto("/learn/enrollments/" + eid + "/lessons/" + lesson.id);
    await s.getByRole("button", { name: "Start quiz", exact: true }).click();
    await s.getByRole("textbox", { name: "Answer to question 1", exact: true }).fill("Python");
    await expect(s.getByText("All answers saved", { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    // Delay subsequent PUTs until after the real deadline. The server must reject them.
    const late = s.waitForResponse((r) => r.url().endsWith("/answers") && r.status() === 409);
    await s.route("**/quiz-attempts/*/answers", async (route) => {
      await new Promise((done) => setTimeout(done, 18_000));
      await route.continue();
    });
    await s
      .getByRole("textbox", { name: "Answer to question 1", exact: true })
      .fill("Unaccepted late edit");
    const rejected = await late;
    expect((await rejected.json()).error.code).toMatch(/^attempt_(expired|closed)$/);
    const result = s.getByRole("region", { name: "Quiz result", exact: true });
    await expect(result).toBeVisible({ timeout: 25_000 });
    await expect(result).toContainText("Passed");
    await expect(result).toContainText("1 / 1");
    await expect(s.getByRole("progressbar", { name: "Course progress" })).toHaveAttribute(
      "aria-valuenow",
      "100",
    );
    await expect(s.getByRole("textbox")).toHaveCount(0);
    await expect(s.getByRole("region", { name: "Correct answers", exact: true })).toHaveCount(0);
    await accessible(s);
  } finally {
    await author.context.close();
    await admin.context.close();
    await student.context.close();
  }
});
