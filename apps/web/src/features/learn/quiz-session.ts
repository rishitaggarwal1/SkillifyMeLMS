import { ApiError } from "@/lib/api/errors";
import type { AnswerInput, QuizAnswer, QuizAttempt, QuizResult } from "./quiz-api";

type Transport = {
  read: () => Promise<QuizAttempt>;
  save: (answers: AnswerInput[], revision: number) => Promise<QuizAttempt>;
  submit: (answers: AnswerInput[], revision: number) => Promise<QuizResult>;
};
type Phase =
  "ready" | "saving" | "saved" | "error" | "conflict" | "submitting" | "submitted" | "denied";
export type QuizSnapshot = {
  attempt: QuizAttempt;
  answers: Record<string, QuizAnswer>;
  remaining: number;
  dirty: number;
  phase: Phase;
  error: string | null;
  result: QuizResult | null;
  announcement: string;
};

/** One active attempt's in-memory edits and serialized conditional writes.
 * Nothing is persisted locally; acknowledged checkpoints and resume use the API. */
export class QuizSession {
  private snapshot: QuizSnapshot;
  private listeners = new Set<() => void>();
  private dirty = new Map<string, number>();
  private generation = 0;
  private pending: Promise<unknown> = Promise.resolve();
  private anchor = 0;
  private duration = 0;
  private finishing = false;
  private running = false;
  private timer?: ReturnType<typeof setInterval>;
  private autosave?: ReturnType<typeof setInterval>;
  private warnings = new Set<number>();
  private retryAt = 0;

  constructor(
    attempt: QuizAttempt,
    private transport: Transport,
    private clock: () => number = () => performance.now(),
  ) {
    this.snapshot = {
      attempt,
      answers: {},
      remaining: 0,
      dirty: 0,
      phase: "ready",
      error: null,
      result: null,
      announcement: "",
    };
    this.adopt(attempt);
  }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private emit(patch: Partial<QuizSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch, dirty: this.dirty.size };
    this.listeners.forEach((listener) => listener());
  }
  private remaining() {
    return Math.max(0, Math.ceil((this.duration - (this.clock() - this.anchor)) / 1000));
  }
  private adopt(attempt: QuizAttempt) {
    this.duration = Math.max(0, Date.parse(attempt.expires_at) - Date.parse(attempt.server_now));
    this.anchor = this.clock();
    const answers = Object.fromEntries(
      attempt.questions.map((q) => [
        q.id,
        this.dirty.has(q.id)
          ? this.snapshot.answers[q.id]!
          : (q.saved_answer ?? { option_ids: [], text: null }),
      ]),
    );
    this.emit({ attempt, answers, remaining: this.remaining() });
  }
  private queue<T>(work: () => Promise<T>) {
    const result = this.pending.then(work);
    this.pending = result.catch(() => {});
    return result;
  }
  start() {
    if (this.running || this.snapshot.attempt.state !== "in_progress") return;
    this.running = true;
    this.timer = setInterval(() => this.tick(), 1000);
    this.autosave = setInterval(() => {
      if (this.snapshot.remaining === 0 && this.clock() >= this.retryAt)
        void this.submit(true).catch(() => {});
      else void this.save().catch(() => {});
    }, 10_000);
    this.tick();
  }
  stop() {
    this.running = false;
    clearInterval(this.timer);
    clearInterval(this.autosave);
  }
  private tick() {
    const remaining = this.remaining();
    let announcement = this.snapshot.announcement;
    for (const threshold of [300, 60, 10]) {
      if (remaining > 0 && remaining <= threshold && !this.warnings.has(threshold)) {
        this.warnings.add(threshold);
        announcement =
          threshold === 300
            ? "Five minutes remaining."
            : threshold === 60
              ? "One minute remaining."
              : "Ten seconds remaining.";
      }
    }
    this.emit({ remaining, announcement });
    if (
      !remaining &&
      this.clock() >= this.retryAt &&
      this.snapshot.attempt.state === "in_progress" &&
      this.snapshot.phase !== "denied"
    ) {
      void this.submit(true).catch(() => {});
    }
  }
  edit(id: string, answer: QuizAnswer) {
    if (
      this.remaining() === 0 ||
      this.finishing ||
      ["conflict", "denied", "submitted"].includes(this.snapshot.phase) ||
      this.snapshot.attempt.state !== "in_progress"
    )
      return;
    if (!this.snapshot.attempt.questions.some((q) => q.id === id)) return;
    this.dirty.set(id, ++this.generation);
    this.emit({
      answers: {
        ...this.snapshot.answers,
        [id]: { ...answer, option_ids: [...(answer.option_ids ?? [])] },
      },
      phase: this.snapshot.phase === "saving" ? "saving" : "ready",
    });
  }
  private batch(): AnswerInput[] {
    return [...this.dirty.keys()].map((id) => ({
      question_id: id,
      answer: this.snapshot.answers[id]!,
    }));
  }
  private fail(error: unknown) {
    if (error instanceof ApiError && error.status === 404) {
      this.stop();
      this.dirty.clear();
      this.emit({
        phase: "denied",
        answers: {},
        error: "This quiz is no longer available.",
        announcement: "",
      });
    } else if (error instanceof ApiError && error.status === 409) {
      if (["attempt_expired", "attempt_closed"].includes(error.code)) {
        this.duration = 0;
        this.emit({
          phase: "error",
          remaining: 0,
          error: "Time has ended or the attempt has closed. Checking the result.",
        });
        void this.refresh().catch(() => {});
      } else {
        this.emit({
          phase: "conflict",
          error:
            "This attempt changed on another device. Refresh to use its saved answers; your unsaved changes will be discarded.",
        });
      }
    } else {
      this.emit({
        phase: "error",
        error:
          this.remaining() === 0
            ? "Time is up. Submission will retry when the connection returns."
            : "Your changes have not been saved. Check your connection and retry.",
      });
    }
  }
  save() {
    return this.queue(async () => {
      if (
        !this.dirty.size ||
        this.finishing ||
        this.snapshot.attempt.state !== "in_progress" ||
        ["conflict", "denied"].includes(this.snapshot.phase) ||
        this.remaining() === 0
      )
        return;
      const sent = new Map(this.dirty);
      this.emit({ phase: "saving", error: null });
      try {
        const attempt = await this.transport.save(this.batch(), this.snapshot.attempt.revision);
        for (const [id, generation] of sent) {
          if (this.dirty.get(id) === generation) this.dirty.delete(id);
        }
        this.adopt(attempt);
        this.emit({
          phase: this.finishing ? "submitting" : this.dirty.size ? "ready" : "saved",
          error: null,
        });
      } catch (error) {
        this.fail(error);
        throw error;
      }
    });
  }
  refresh(discard = false) {
    return this.queue(async () => {
      if (this.snapshot.phase === "denied") return;
      try {
        const attempt = await this.transport.read();
        if (
          attempt.state === "in_progress" &&
          !discard &&
          this.dirty.size &&
          attempt.revision !== this.snapshot.attempt.revision
        ) {
          this.emit({
            phase: "conflict",
            error:
              "This attempt changed on another device. Refresh to use its saved answers; your unsaved changes will be discarded.",
          });
          return;
        }
        if (discard || attempt.state !== "in_progress") this.dirty.clear();
        this.adopt(attempt);
        this.emit({
          phase:
            attempt.state === "in_progress"
              ? this.finishing
                ? "submitting"
                : "ready"
              : "submitted",
          error: null,
        });
        if (attempt.state !== "in_progress") this.stop();
      } catch (error) {
        this.fail(error);
        throw error;
      }
    });
  }
  submit(expired = false): Promise<QuizResult | undefined> {
    if (
      this.finishing ||
      this.snapshot.attempt.state !== "in_progress" ||
      this.snapshot.phase === "denied"
    ) {
      return Promise.resolve(this.snapshot.result ?? undefined);
    }
    if (!expired && this.snapshot.phase === "conflict") return Promise.resolve(undefined);
    this.finishing = true;
    this.emit({ phase: "submitting", error: null });
    return this.queue(async () => {
      try {
        // After expiry only the server's accepted checkpoint may be scored.
        if (expired || this.remaining() === 0) {
          const current = await this.transport.read();
          this.dirty.clear();
          this.adopt(current);
          if (current.state !== "in_progress") {
            this.emit({ phase: "submitted", error: null });
            this.stop();
            return undefined;
          }
        }
        const result = await this.transport.submit(
          expired || this.remaining() === 0 ? [] : this.batch(),
          this.snapshot.attempt.revision,
        );
        this.dirty.clear();
        this.emit({ phase: "submitted", result, error: null, announcement: "Quiz submitted." });
        this.stop();
        return result;
      } catch (error) {
        this.retryAt = this.clock() + 5000;
        this.fail(error);
        throw error;
      } finally {
        this.finishing = false;
      }
    });
  }
}
