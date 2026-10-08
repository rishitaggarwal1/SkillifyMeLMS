"use client";

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

import { ConfirmButton, ErrorAlert, LoadMore } from "@/components/patterns/page";
import {
  FormField,
  LiveAnnouncement,
  PageSkeleton,
  StatusBadge,
} from "@/components/patterns/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/errors";
import { formatIst } from "@/lib/ist";

import { learnKeys } from "./api";
import {
  quizHistoryQuery,
  quizKeys,
  quizResultQuery,
  quizTransport,
  readQuizAttempt,
  startQuizAttempt,
  studentQuizQuery,
  type QuizAnswer,
  type QuizAttempt,
  type QuizQuestion,
  type QuizResult,
} from "./quiz-api";
import { QuizSession } from "./quiz-session";

type Props = { enrollmentId: string; lessonId: string; completed?: boolean };

export default function QuizLesson({ enrollmentId, lessonId, completed = false }: Props) {
  const qc = useQueryClient();
  const rules = useQuery({ ...studentQuizQuery(enrollmentId, lessonId), refetchInterval: 30_000 });
  const [attempt, setAttempt] = useState<QuizAttempt | null>(null);
  const [resultId, setResultId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  function refreshProgress() {
    void qc.invalidateQueries({ queryKey: learnKeys.enrollment(enrollmentId) });
    void qc.invalidateQueries({ queryKey: learnKeys.enrollments, exact: true });
    void qc.invalidateQueries({ queryKey: ["dashboards", "results"] });
    void qc.invalidateQueries({ queryKey: quizKeys.rules(enrollmentId, lessonId) });
    void qc.invalidateQueries({ queryKey: quizKeys.history(enrollmentId, lessonId) });
  }
  async function begin() {
    setBusy(true);
    setError(null);
    try {
      const current = await qc.fetchQuery(studentQuizQuery(enrollmentId, lessonId));
      const next = current.active_attempt_id
        ? await readQuizAttempt(current.active_attempt_id)
        : await startQuizAttempt(enrollmentId, lessonId, current.revision);
      if (next.state === "submitted") setResultId(next.id);
      else setAttempt(next);
      refreshProgress();
    } catch (e) {
      setError(e);
      void rules.refetch();
    } finally {
      setBusy(false);
    }
  }
  if (rules.isPending) return <PageSkeleton kind="form" />;
  if (!rules.data || (rules.error instanceof ApiError && rules.error.status === 404))
    return <ErrorAlert error={rules.error} onRetry={() => void rules.refetch()} />;
  if (attempt)
    return (
      <>
        {rules.error ? (
          <ErrorAlert error={rules.error} onRetry={() => void rules.refetch()} />
        ) : null}
        <ActiveQuiz
          key={attempt.id}
          initial={attempt}
          onFinished={() => {
            setAttempt(null);
            setResultId(attempt.id);
            refreshProgress();
          }}
        />
      </>
    );
  if (resultId)
    return (
      <QuizResults
        attemptId={resultId}
        remaining={rules.data.attempts_remaining}
        active={!!rules.data.active_attempt_id}
        onBack={() => setResultId(null)}
        onStart={() => void begin()}
        busy={busy}
        error={error}
        completed={completed}
      />
    );
  const quiz = rules.data;
  return (
    <section className="flex flex-col gap-5" aria-label="Quiz">
      {rules.error ? <ErrorAlert error={rules.error} onRetry={() => void rules.refetch()} /> : null}
      <h3>{quiz.title}</h3>
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="state-card">
          <dt className="text-sm text-muted-foreground">Time limit</dt>
          <dd>
            {Math.ceil(quiz.time_limit_seconds / 60)} min ({quiz.time_limit_seconds} seconds)
          </dd>
        </div>
        <div className="state-card">
          <dt className="text-sm text-muted-foreground">Pass mark</dt>
          <dd>
            {Number(quiz.pass_marks)} / {Number(quiz.max_marks)}
          </dd>
        </div>
        <div className="state-card">
          <dt className="text-sm text-muted-foreground">Attempts</dt>
          <dd>
            {quiz.attempts_used} / {quiz.attempts_allowed} used · {quiz.attempts_remaining}{" "}
            remaining
          </dd>
        </div>
      </dl>
      <p className="text-sm text-muted-foreground">
        Starting uses one attempt. The timer continues if you leave. Answers save every 10 seconds.
        This lesson completes when you pass.
      </p>
      <p className="text-sm text-muted-foreground">
        {revealDescription(quiz.reveal_mode, quiz.reveal_timing)}
      </p>
      {error ? <ErrorAlert error={error} onRetry={() => void begin()} /> : null}
      <div>
        <Button
          disabled={busy || (!quiz.active_attempt_id && quiz.attempts_remaining === 0)}
          onClick={() => void begin()}
        >
          {busy ? "Opening…" : quiz.active_attempt_id ? "Resume quiz" : "Start quiz"}
        </Button>
      </div>
      {!quiz.active_attempt_id && !quiz.attempts_remaining ? (
        <p>All attempts have been used.</p>
      ) : null}
      <QuizHistory
        enrollmentId={enrollmentId}
        lessonId={lessonId}
        onResult={setResultId}
        onResume={() => void begin()}
      />
    </section>
  );
}

function revealDescription(mode: string, timing: string) {
  if (mode === "score_only") return "Results show your score only.";
  return (
    (mode === "explanations" ? "Correct answers and explanations" : "Correct answers") +
    (timing === "immediately"
      ? " are available after submission."
      : " are available after all attempts have been used and no attempt is active.")
  );
}

function ActiveQuiz({ initial, onFinished }: { initial: QuizAttempt; onFinished: () => void }) {
  const router = useRouter();
  const [session] = useState(() => new QuizSession(initial, quizTransport(initial.id)));
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  const [leaving, setLeaving] = useState<string | null>(null);
  const [navigating, setNavigating] = useState(false);
  const blocked =
    navigating ||
    state.remaining === 0 ||
    ["submitting", "submitted", "conflict", "denied"].includes(state.phase);
  useEffect(() => {
    session.start();
    const refresh = () => {
      if (document.visibilityState === "visible") void session.refresh().catch(() => {});
    };
    const hidden = () => {
      if (document.visibilityState === "hidden") void session.save().catch(() => {});
      else refresh();
    };
    const unload = (event: BeforeUnloadEvent) => {
      if (session.getSnapshot().dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const save = () => {
      void session.save().catch(() => {});
    };
    const timer = setInterval(refresh, 30_000);
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("focus", refresh);
    window.addEventListener("pagehide", save);
    window.addEventListener("beforeunload", unload);
    return () => {
      session.stop();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("pagehide", save);
      window.removeEventListener("beforeunload", unload);
    };
  }, [session]);
  useEffect(() => {
    if (state.phase === "submitted" && state.attempt.state !== "abandoned") onFinished();
  }, [state.phase, state.attempt.state, onFinished]);
  useEffect(() => {
    const click = (event: MouseEvent) => {
      if (
        !session.getSnapshot().dirty ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const link = (event.target as Element).closest<HTMLAnchorElement>("a[href]");
      if (!link || link.target === "_blank" || link.hasAttribute("download")) return;
      const destination = new URL(link.href, window.location.href);
      if (destination.href === window.location.href) return;
      event.preventDefault();
      event.stopPropagation();
      const href =
        destination.origin === window.location.origin
          ? destination.pathname + destination.search + destination.hash
          : destination.href;
      setLeaving(href);
      setNavigating(true);
      void session
        .save()
        .then(() => {
          if (session.getSnapshot().dirty) return;
          router.push(href);
        })
        .catch(() => {})
        .finally(() => setNavigating(false));
    };
    document.addEventListener("click", click, true);
    return () => document.removeEventListener("click", click, true);
  }, [router, session]);
  return (
    <section className="flex flex-col gap-5" aria-label="Active quiz">
      <div className="sticky top-32 z-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-background p-3">
        <p className="font-semibold">Attempt {state.attempt.attempt_number}</p>
        <p
          role="timer"
          aria-live="off"
          aria-label="Time remaining"
          className="font-mono text-lg tabular-nums"
        >
          {Math.floor(state.remaining / 60)}:{String(state.remaining % 60).padStart(2, "0")}
        </p>
        <p role="status" className="text-sm text-muted-foreground">
          {state.phase === "saving"
            ? "Saving…"
            : state.phase === "saved"
              ? "All answers saved"
              : state.phase === "submitting"
                ? "Submitting…"
                : state.dirty
                  ? "Unsaved changes"
                  : "Answers saved"}
        </p>
      </div>
      <LiveAnnouncement>{state.announcement}</LiveAnnouncement>
      {state.error ? (
        <div className="state-card flex flex-col gap-3" role="alert">
          <p>{state.error}</p>
          {state.phase === "conflict" ? (
            <ConfirmButton
              label="Refresh saved answers"
              title="Discard unsaved changes?"
              description="Load the latest saved answers from the server. Local unsaved changes will be lost."
              confirmLabel="Refresh saved answers"
              variant="outline"
              onConfirm={() => session.refresh(true)}
            />
          ) : state.phase !== "denied" ? (
            <Button
              variant="outline"
              className="w-fit"
              onClick={() =>
                void (state.remaining ? session.save() : session.submit(true)).catch(() => {})
              }
            >
              Retry
            </Button>
          ) : null}
        </div>
      ) : null}
      {leaving && state.dirty ? (
        <div className="state-card flex flex-col gap-3" aria-label="Unsaved navigation">
          <p>
            {navigating
              ? "Saving before leaving…"
              : "Stay here to retry saving, or leave without your unsaved changes."}
          </p>
          {!navigating ? (
            <ConfirmButton
              label="Leave without saving"
              title="Leave without saving?"
              description="Only the last accepted answers will be available when you resume. The timer will continue."
              confirmLabel="Leave without saving"
              onConfirm={async () => {
                router.push(leaving);
              }}
            />
          ) : null}
        </div>
      ) : null}
      {state.phase !== "denied"
        ? state.attempt.questions.map((question, i) => (
            <QuizQuestionField
              key={question.id}
              question={question}
              index={i}
              answer={state.answers[question.id] ?? {}}
              disabled={blocked}
              onChange={(answer) => session.edit(question.id, answer)}
            />
          ))
        : null}
      {state.attempt.state === "abandoned" ? (
        <p>This attempt is closed.</p>
      ) : blocked ? (
        <p className="text-sm text-muted-foreground">
          {state.remaining === 0
            ? "Time has ended. Your accepted answers are being finalized."
            : "Answers are currently locked."}
        </p>
      ) : (
        <div>
          <ConfirmButton
            label="Submit quiz"
            title="Submit this attempt?"
            description="You cannot change your answers after submission."
            confirmLabel="Submit attempt"
            variant="outline"
            size="default"
            onConfirm={() => session.submit()}
          />
        </div>
      )}
    </section>
  );
}

export function QuizQuestionField({
  question,
  index,
  answer,
  disabled,
  onChange,
}: {
  question: QuizQuestion;
  index: number;
  answer: QuizAnswer;
  disabled: boolean;
  onChange: (answer: QuizAnswer) => void;
}) {
  const ids = answer.option_ids ?? [];
  return (
    <fieldset disabled={disabled} className="state-card flex min-w-0 flex-col gap-3">
      <legend className="px-1 font-semibold">
        Question {index + 1} · {Number(question.marks)} marks
      </legend>
      <p className="break-words whitespace-pre-wrap">{question.prompt}</p>
      {question.question_type === "fill_blank" ? (
        <FormField
          label={"Answer to question " + (index + 1)}
          help="Enter your answer. Up to 20,000 characters."
          disabled={disabled}
        >
          {(props) => (
            <Input
              {...props}
              value={answer.text ?? ""}
              maxLength={20_000}
              autoComplete="off"
              onChange={(event) => onChange({ text: event.target.value, option_ids: [] })}
            />
          )}
        </FormField>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            {question.question_type === "mcq_single"
              ? "Choose one answer."
              : "Choose all that apply. Partial marks are possible."}
          </p>
          {(question.options ?? []).map((option) => (
            <label
              key={option.id}
              className="flex min-h-11 cursor-pointer items-center gap-3 rounded-md border p-3"
            >
              <input
                type={question.question_type === "mcq_single" ? "radio" : "checkbox"}
                name={"question-" + question.id}
                value={option.id}
                checked={ids.includes(option.id)}
                className="size-5 shrink-0 accent-primary"
                onChange={() =>
                  onChange({
                    text: null,
                    option_ids:
                      question.question_type === "mcq_single"
                        ? [option.id]
                        : ids.includes(option.id)
                          ? ids.filter((id) => id !== option.id)
                          : [...ids, option.id],
                  })
                }
              />
              <span className="min-w-0 break-words whitespace-pre-wrap">{option.text}</span>
            </label>
          ))}
        </>
      )}
    </fieldset>
  );
}

function QuizHistory({
  enrollmentId,
  lessonId,
  onResult,
  onResume,
}: Props & {
  onResult: (id: string) => void;
  onResume: () => void;
}) {
  const history = useInfiniteQuery(quizHistoryQuery(enrollmentId, lessonId));
  return (
    <section className="flex flex-col gap-3" aria-label="Quiz attempt history">
      <h3>Attempt history</h3>
      {history.isPending ? (
        <PageSkeleton rows={1} />
      ) : history.error ? (
        <ErrorAlert error={history.error} onRetry={() => void history.refetch()} />
      ) : !history.data.pages.some((p) => p.items.length) ? (
        <p className="text-sm text-muted-foreground">You have not started this quiz.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {history.data.pages
            .flatMap((p) => p.items)
            .map((a) => (
              <li
                key={a.id}
                className="state-card flex flex-wrap items-center justify-between gap-3"
              >
                <div>
                  <p>
                    Attempt {a.attempt_number} · {formatIst(a.started_at)}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    {a.state === "submitted"
                      ? Number(a.score) +
                        " / " +
                        Number(a.max_marks) +
                        (a.passed ? " · Passed" : " · Not passed")
                      : a.state === "abandoned"
                        ? "Closed"
                        : "In progress"}
                  </p>
                </div>
                {a.state === "submitted" ? (
                  <Button variant="outline" onClick={() => onResult(a.id)}>
                    {"View result for attempt " + a.attempt_number}
                  </Button>
                ) : a.state === "in_progress" ? (
                  <Button variant="outline" onClick={onResume}>
                    Resume quiz
                  </Button>
                ) : null}
              </li>
            ))}
        </ul>
      )}
      <LoadMore
        hasNextPage={history.hasNextPage}
        isFetchingNextPage={history.isFetchingNextPage}
        onClick={() => void history.fetchNextPage()}
      />
    </section>
  );
}

/** Defense in depth: never render unexpected reveal data, even from a malformed response. */
export function solutionsAllowed(result: QuizResult) {
  const c = result.context;
  return (
    c.state === "submitted" &&
    c.configured_reveal_mode !== "score_only" &&
    result.reveal_mode !== "score_only" &&
    (c.reveal_timing === "immediately" ||
      (c.attempts_used >= c.attempts_allowed && !c.has_active_attempt))
  );
}
function QuizResults({
  attemptId,
  remaining,
  active,
  onBack,
  onStart,
  busy,
  error,
  completed,
}: {
  attemptId: string;
  remaining: number;
  active: boolean;
  onBack: () => void;
  onStart: () => void;
  busy: boolean;
  error: unknown;
  completed: boolean;
}) {
  const result = useQuery(quizResultQuery(attemptId));
  const questions = useQuery({
    queryKey: ["learn", "quiz-result-questions", attemptId],
    queryFn: () => readQuizAttempt(attemptId),
    enabled: !!result.data && solutionsAllowed(result.data),
    staleTime: 0,
    gcTime: 0,
  });
  if (result.isPending) return <PageSkeleton kind="form" />;
  if (result.error)
    return <ErrorAlert error={result.error} onRetry={() => void result.refetch()} />;
  return (
    <section className="flex flex-col gap-5" aria-label="Quiz result">
      <h3>Quiz result</h3>
      <StatusBadge kind={result.data.passed ? "success" : "warning"}>
        {result.data.passed ? "Passed" : "Not passed"}
      </StatusBadge>
      <p className="text-2xl font-semibold tabular-nums">
        {Number(result.data.score)} / {Number(result.data.max_marks)}
      </p>
      <p className="text-sm">
        Pass mark: {Number(result.data.pass_marks)} · {remaining} attempts remaining
      </p>
      <p className="text-sm text-muted-foreground">
        {completed || result.data.passed
          ? "This lesson is complete."
          : "Pass this quiz to complete the lesson."}
      </p>
      <QuizSolutions result={result.data} questions={questions.data?.questions ?? []} />
      {solutionsAllowed(result.data) && questions.isPending ? <PageSkeleton rows={1} /> : null}
      {questions.error ? (
        <ErrorAlert error={questions.error} onRetry={() => void questions.refetch()} />
      ) : null}
      {error ? <ErrorAlert error={error} onRetry={onStart} /> : null}
      <div className="flex flex-wrap gap-3">
        <Button variant="outline" onClick={onBack}>
          Back to quiz
        </Button>
        {remaining > 0 || active ? (
          <Button onClick={onStart} disabled={busy}>
            {busy ? "Opening…" : active ? "Resume quiz" : "Try again"}
          </Button>
        ) : null}
      </div>
    </section>
  );
}
export function QuizSolutions({
  result,
  questions,
}: {
  result: QuizResult;
  questions: QuizQuestion[];
}) {
  if (!solutionsAllowed(result) || result.reveal_mode === "score_only") return null;
  return (
    <section aria-label="Correct answers" className="flex flex-col gap-3">
      <h4>Correct answers</h4>
      {result.solutions.map((solution, i) => {
        const question = questions.find((q) => q.id === solution.question_id);
        if (!question) return null;
        const explanation =
          result.reveal_mode === "explanations" &&
          result.context.configured_reveal_mode === "explanations" &&
          "explanation" in solution
            ? String(solution.explanation)
            : null;
        return (
          <div className="state-card flex flex-col gap-2" key={solution.question_id}>
            <p className="font-semibold">Question {i + 1}</p>
            <p className="break-words whitespace-pre-wrap">{question.prompt}</p>
            <p className="break-words whitespace-pre-wrap">
              {question.question_type === "fill_blank"
                ? (solution.answer_key.accepted_answers ?? []).join(" / ")
                : (question.options ?? [])
                    .filter((o) => (solution.answer_key.correct_option_ids ?? []).includes(o.id))
                    .map((o) => o.text)
                    .join(" · ")}
            </p>
            {explanation ? <p className="break-words whitespace-pre-wrap">{explanation}</p> : null}
          </div>
        );
      })}
    </section>
  );
}
