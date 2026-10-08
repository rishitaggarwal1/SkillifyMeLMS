import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { unwrap } from "@/lib/api/unwrap";

// Student projections only. Never import author question/quiz contracts here.
export type StudentQuiz = components["schemas"]["StudentQuiz"];
export type QuizAttempt = components["schemas"]["AttemptDetail"];
export type QuizSummary = components["schemas"]["AttemptSummary"];
export type QuizQuestion = components["schemas"]["ActiveQuestion"];
export type QuizAnswer = components["schemas"]["SavedAnswer"];
export type AnswerInput = components["schemas"]["AnswerInput"];
export type QuizResult =
  | components["schemas"]["ScoreResult"]
  | components["schemas"]["AnswersResult"]
  | components["schemas"]["ExplanationsResult"];

const answerBatch = z.object({
  answers: z
    .array(
      z.object({
        question_id: z.uuid(),
        answer: z.object({
          option_ids: z.array(z.string().min(1).max(100)).max(100).optional(),
          text: z.string().max(20_000).nullable().optional(),
        }),
      }),
    )
    .max(100),
});

const lessonPath = (enrollmentId: string, lessonId: string) => ({
  enrollment_id: enrollmentId,
  lesson_id: lessonId,
});
export const quizKeys = {
  rules: (e: string, l: string) => ["learn", "quiz", e, l] as const,
  history: (e: string, l: string) => ["learn", "quiz-history", e, l] as const,
  result: (id: string) => ["learn", "quiz-result", id] as const,
};
export const studentQuizQuery = (e: string, l: string) =>
  queryOptions({
    queryKey: quizKeys.rules(e, l),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/quiz", {
          params: { path: lessonPath(e, l) },
        }),
      ),
    staleTime: 0,
  });
export const quizHistoryQuery = (e: string, l: string) =>
  infiniteQueryOptions({
    queryKey: quizKeys.history(e, l),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/quiz-attempts", {
          params: { path: lessonPath(e, l), query: { limit: 25, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 0,
  });
export const quizResultQuery = (id: string) =>
  queryOptions({
    queryKey: quizKeys.result(id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/quiz-attempts/{attempt_id}/results", {
          params: { path: { attempt_id: id } },
        }),
      ),
    staleTime: 0,
    gcTime: 0,
  });
export const readQuizAttempt = (id: string) =>
  unwrap(
    api.GET("/api/v1/quiz-attempts/{attempt_id}", {
      params: { path: { attempt_id: id } },
    }),
  );
export const startQuizAttempt = (e: string, l: string, revision: number) =>
  unwrap(
    api.POST("/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/quiz-attempts", {
      params: { path: lessonPath(e, l), header: { "If-Match": String(revision) } },
    }),
  );
export const quizTransport = (id: string) => ({
  read: () => readQuizAttempt(id),
  save: (answers: AnswerInput[], revision: number) => {
    const body = answerBatch.parse({ answers });
    return unwrap(
      api.PUT("/api/v1/quiz-attempts/{attempt_id}/answers", {
        params: { path: { attempt_id: id }, header: { "If-Match": String(revision) } },
        body,
        // Keep large batches on ordinary fetch; unload requests have a small body budget.
        keepalive: new TextEncoder().encode(JSON.stringify(body)).byteLength <= 60 * 1024,
      }),
    );
  },
  submit: (answers: AnswerInput[], revision: number) =>
    unwrap(
      api.POST("/api/v1/quiz-attempts/{attempt_id}/submit", {
        params: { path: { attempt_id: id }, header: { "If-Match": String(revision) } },
        body: answerBatch.parse({ answers }),
      }),
    ),
});
