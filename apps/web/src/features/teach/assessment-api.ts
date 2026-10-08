import {
  infiniteQueryOptions,
  queryOptions,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type {
  AuthorQuestion,
  DraftQuiz,
  QuestionBank,
  QuestionBody,
  QuizBody,
} from "@/lib/api/types";
import { unwrap } from "@/lib/api/unwrap";

import { keys, useOutlineEdit, type IfMatch } from "./api";

export const bankKeys = {
  all: ["teach", "question-banks"] as const,
  bank: (id: string) => ["teach", "question-banks", id] as const,
  questions: (id: string) => ["teach", "question-banks", id, "questions"] as const,
};
export const banksQuery = (q = "") =>
  infiniteQueryOptions({
    queryKey: [...bankKeys.all, { q }] as const,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/question-banks", {
          params: { query: { q: q || undefined, limit: 25, cursor: pageParam } },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
export const bankQuery = (id: string) =>
  queryOptions({
    queryKey: bankKeys.bank(id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/question-banks/{bank_id}", { params: { path: { bank_id: id } } })),
  });
export type QuestionFilters = {
  q?: string;
  kind?: QuestionBody["question_type"];
  skills?: string[];
};
export const bankQuestionsQuery = (bankId: string, filters: QuestionFilters = {}) =>
  infiniteQueryOptions({
    queryKey: [...bankKeys.questions(bankId), filters] as const,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/question-banks/{bank_id}/questions", {
          params: {
            path: { bank_id: bankId },
            query: {
              q: filters.q || undefined,
              question_type: filters.kind,
              skill_ids: filters.skills,
              limit: 25,
              cursor: pageParam,
            },
          },
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
export function useCreateBank() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description: string }) =>
      unwrap(
        api.POST("/api/v1/question-banks", {
          body,
          params: { header: { "If-Match": "0" } },
        }),
      ),
    onSuccess: (saved) => {
      qc.setQueryData(bankKeys.bank(saved.id), saved);
      return qc.invalidateQueries({ queryKey: bankKeys.all });
    },
  });
}

/** Queue every mutation of one bank; retain the user's draft on conflict. */
export function useBankEdit<V, R>(
  bankId: string,
  run: (values: V, header: IfMatch) => Promise<R>,
  revisionOf: (saved: R, previous: number) => number,
  refreshBank = true,
) {
  const qc = useQueryClient();
  return useMutation({
    scope: { id: "bank:" + bankId },
    mutationFn: async (values: V) => {
      await qc.cancelQueries({ queryKey: bankKeys.bank(bankId) });
      const bank =
        qc.getQueryData<QuestionBank>(bankKeys.bank(bankId)) ??
        (await qc.fetchQuery(bankQuery(bankId)));
      const saved = await run(values, { "If-Match": String(bank.revision) });
      qc.setQueryData<QuestionBank>(bankKeys.bank(bankId), (old) =>
        old ? { ...old, revision: revisionOf(saved, bank.revision) } : old,
      );
      return saved;
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) {
        await qc.invalidateQueries({ queryKey: bankKeys.bank(bankId), exact: true });
      }
    },
    onSuccess: () =>
      qc.invalidateQueries({
        queryKey: bankKeys.all,
        predicate: (query) => refreshBank || query.queryKey[2] !== bankId,
      }),
  });
}
export function useUpdateBank(bankId: string) {
  return useBankEdit(
    bankId,
    (body: { name: string; description: string }, header: IfMatch) =>
      unwrap(
        api.PATCH("/api/v1/question-banks/{bank_id}", {
          body,
          params: { path: { bank_id: bankId }, header },
        }),
      ),
    (saved) => saved.revision,
  );
}
export function useArchiveBank(bankId: string) {
  return useBankEdit(
    bankId,
    (_: void, header: IfMatch) =>
      unwrap(
        api.DELETE("/api/v1/question-banks/{bank_id}", {
          params: { path: { bank_id: bankId }, header },
        }),
      ),
    (_saved, previous) => previous + 1,
    false,
  );
}
export function useSaveQuestion(bankId: string) {
  return useBankEdit(
    bankId,
    (
      { id, body }: { id?: string; body: QuestionBody },
      header: IfMatch,
    ): Promise<AuthorQuestion> =>
      id
        ? unwrap(
            api.PATCH("/api/v1/questions/{question_id}", {
              body,
              params: { path: { question_id: id }, header },
            }),
          )
        : unwrap(
            api.POST("/api/v1/question-banks/{bank_id}/questions", {
              body,
              params: { path: { bank_id: bankId }, header },
            }),
          ),
    (saved) => saved.bank_revision,
  );
}
export function useArchiveQuestion(bankId: string) {
  return useBankEdit(
    bankId,
    (id: string, header: IfMatch) =>
      unwrap(
        api.DELETE("/api/v1/questions/{question_id}", {
          params: { path: { question_id: id }, header },
        }),
      ),
    (_saved, previous) => previous + 1,
  );
}
export function useQuestionSkills(bankId: string) {
  return useBankEdit(
    bankId,
    ({ id, skills }: { id: string; skills: string[] }, header: IfMatch) =>
      unwrap(
        api.PUT("/api/v1/questions/{question_id}/skills", {
          body: { skill_ids: skills },
          params: { path: { question_id: id }, header },
        }),
      ),
    (saved) => saved.bank_revision,
  );
}
export const draftQuizQuery = (courseId: string, lessonId: string) =>
  queryOptions({
    queryKey: [...keys.lesson(courseId, lessonId), "quiz"] as const,
    queryFn: async (): Promise<DraftQuiz | null> => {
      try {
        return await unwrap(
          api.GET("/api/v1/courses/{course_id}/lessons/{lesson_id}/quiz", {
            params: { path: { course_id: courseId, lesson_id: lessonId } },
          }),
        );
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
  });
export function useSaveQuiz(courseId: string, lessonId: string) {
  const qc = useQueryClient();
  return useOutlineEdit(
    courseId,
    (body: QuizBody, header: IfMatch) =>
      unwrap(
        api.PUT("/api/v1/courses/{course_id}/lessons/{lesson_id}/quiz", {
          body,
          params: { path: { course_id: courseId, lesson_id: lessonId }, header },
        }),
      ),
    {
      revisionOf: (saved) => saved.course_revision,
      onSuccess: (saved) => qc.setQueryData(draftQuizQuery(courseId, lessonId).queryKey, saved),
    },
  );
}
