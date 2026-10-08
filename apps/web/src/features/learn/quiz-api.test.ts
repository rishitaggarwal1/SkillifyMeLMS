import { beforeEach, expect, it, vi } from "vitest";
import { quizTransport, startQuizAttempt } from "./quiz-api";

const { get, put, post } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get, PUT: put, POST: post } }));
const questionId = "01900000-0000-7000-8000-000000000001";
beforeEach(() => {
  for (const request of [get, put, post])
    request.mockReset().mockResolvedValue({
      data: {},
      response: new Response(null, { status: 200 }),
    });
});
it("uses the live If-Match revision on start, autosave and submit, and resumes through the API", async () => {
  await startQuizAttempt("e", "l", 2);
  const transport = quizTransport("a");
  await transport.read();
  const answers = [{ question_id: questionId, answer: { text: "Python" } }];
  await transport.save(answers, 4);
  await transport.submit(answers, 5);
  expect(post).toHaveBeenNthCalledWith(
    1,
    "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/quiz-attempts",
    { params: { path: { enrollment_id: "e", lesson_id: "l" }, header: { "If-Match": "2" } } },
  );
  expect(get).toHaveBeenCalledExactlyOnceWith("/api/v1/quiz-attempts/{attempt_id}", {
    params: { path: { attempt_id: "a" } },
  });
  expect(put).toHaveBeenCalledExactlyOnceWith("/api/v1/quiz-attempts/{attempt_id}/answers", {
    params: { path: { attempt_id: "a" }, header: { "If-Match": "4" } },
    body: { answers },
    keepalive: true,
  });
  expect(post).toHaveBeenNthCalledWith(2, "/api/v1/quiz-attempts/{attempt_id}/submit", {
    params: { path: { attempt_id: "a" }, header: { "If-Match": "5" } },
    body: { answers },
  });
});
it("lets large valid answer batches save through an ordinary request", async () => {
  await quizTransport("a").save(
    [
      {
        question_id: questionId,
        answer: { text: "界".repeat(20_000) },
      },
      { question_id: "01900000-0000-7000-8000-000000000002", answer: { text: "界".repeat(1000) } },
    ],
    1,
  );
  // Multibyte text exceeds the bounded unload budget even though each answer is valid.
  expect(put.mock.calls[0]?.[1].keepalive).toBe(false);
});
it("rejects oversized fill answers before issuing a write", () => {
  expect(() =>
    quizTransport("a").save(
      [
        {
          question_id: questionId,
          answer: { text: "x".repeat(20_001) },
        },
      ],
      1,
    ),
  ).toThrow();
  expect(put).not.toHaveBeenCalled();
});
