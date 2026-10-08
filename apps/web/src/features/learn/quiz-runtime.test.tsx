import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import QuizLesson from "./quiz-lesson";
import type { QuizAttempt, StudentQuiz } from "./quiz-api";
import { quizKeys } from "./quiz-api";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get, POST: post } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const rules: StudentQuiz = {
  quiz_id: "q",
  quiz_version_id: "v",
  title: "Knowledge check",
  time_limit_seconds: 600,
  max_marks: "1",
  pass_marks: "1",
  attempts_allowed: 2,
  attempts_used: 0,
  attempts_remaining: 2,
  revision: 0,
  active_attempt_id: null,
  reveal_mode: "score_only",
  reveal_timing: "immediately",
  server_now: "2026-10-08T00:00:00Z",
};
const attempt: QuizAttempt = {
  id: "attempt",
  quiz_version_id: "v",
  attempt_number: 1,
  major_version: 1,
  state: "in_progress",
  revision: 1,
  started_at: rules.server_now,
  server_now: rules.server_now,
  expires_at: "2026-10-08T00:10:00Z",
  submitted_at: null,
  score: null,
  max_marks: "1",
  passed: null,
  questions: [
    {
      id: "01900000-0000-7000-8000-000000000001",
      prompt: "Language",
      question_type: "fill_blank",
      marks: "1",
      options: [],
      skill_ids: [],
      saved_answer: null,
    },
  ],
};
const ok = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) });
it("keeps an active attempt and its unsaved answers mounted when the background rules read fails", async () => {
  get.mockImplementation((path: string) =>
    Promise.resolve(ok(path.endsWith("/quiz") ? rules : { items: [], next_cursor: null })),
  );
  post.mockResolvedValue(ok(attempt));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <QuizLesson enrollmentId="e" lessonId="l" />
    </QueryClientProvider>,
  );
  try {
    fireEvent.click(await screen.findByRole("button", { name: "Start quiz" }));
    const answer = await screen.findByLabelText("Answer to question 1");
    fireEvent.change(answer, { target: { value: "Unsaved Python" } });
    get.mockRejectedValueOnce(new Error("Rules refresh unavailable"));
    await act(async () => {
      await client.refetchQueries({ queryKey: quizKeys.rules("e", "l"), exact: true });
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Rules refresh unavailable");
    expect(screen.getByLabelText("Answer to question 1")).toBe(answer);
    expect(answer).toHaveValue("Unsaved Python");
    expect(answer).toBeEnabled();
    expect(screen.getByRole("region", { name: "Active quiz" })).toBeInTheDocument();
  } finally {
    view.unmount();
    client.clear();
  }
});

it("keeps the lesson complete after a failed retake when enrollment progress already records a pass", async () => {
  get.mockImplementation((path: string) =>
    Promise.resolve(
      ok(
        path.endsWith("/quiz")
          ? { ...rules, attempts_used: 2, attempts_remaining: 0, revision: 2 }
          : path.endsWith("/results")
            ? {
                attempt_id: "attempt",
                reveal_mode: "score_only",
                score: "0",
                max_marks: "1",
                pass_marks: "1",
                passed: false,
                context: {
                  state: "submitted",
                  configured_reveal_mode: "score_only",
                  reveal_timing: "immediately",
                  attempts_allowed: 2,
                  attempts_used: 2,
                  has_active_attempt: false,
                },
              }
            : {
                items: [
                  { ...attempt, state: "submitted", attempt_number: 2, score: "0", passed: false },
                ],
                next_cursor: null,
              },
      ),
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <QuizLesson enrollmentId="e" lessonId="l" completed />
    </QueryClientProvider>,
  );
  try {
    fireEvent.click(await screen.findByRole("button", { name: "View result for attempt 2" }));
    const result = await screen.findByRole("region", { name: "Quiz result" });
    expect(within(result).getByText("Not passed")).toBeInTheDocument();
    expect(within(result).getByText("This lesson is complete.")).toBeInTheDocument();
    expect(
      within(result).queryByText("Pass this quiz to complete the lesson."),
    ).not.toBeInTheDocument();
  } finally {
    view.unmount();
    client.clear();
  }
});
