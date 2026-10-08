import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import type { QuizAttempt, QuizResult } from "./quiz-api";
import { QuizSession } from "./quiz-session";

const attempt = (over: Partial<QuizAttempt> = {}): QuizAttempt => ({
  id: "attempt",
  quiz_version_id: "version",
  attempt_number: 1,
  major_version: 1,
  state: "in_progress",
  revision: 0,
  started_at: "2026-10-08T00:00:00Z",
  server_now: "2026-10-08T00:00:00Z",
  expires_at: "2026-10-08T00:01:00Z",
  submitted_at: null,
  score: null,
  max_marks: "1",
  passed: null,
  questions: [
    {
      id: "q",
      question_type: "fill_blank",
      prompt: "Answer",
      options: [],
      skill_ids: [],
      marks: "1",
      saved_answer: null,
    },
  ],
  ...over,
});
const result: QuizResult = {
  attempt_id: "attempt",
  reveal_mode: "score_only",
  score: "1",
  max_marks: "1",
  pass_marks: "1",
  passed: true,
  context: {
    state: "submitted",
    configured_reveal_mode: "score_only",
    reveal_timing: "immediately",
    attempts_allowed: 2,
    attempts_used: 1,
    has_active_attempt: false,
  },
};
const conflict = () => new ApiError({ status: 409, code: "revision_conflict", message: "Stale" });
const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

describe("Postgres quiz checkpoint session", () => {
  let now: number;
  let transport: {
    read: ReturnType<typeof vi.fn<() => Promise<QuizAttempt>>>;
    save: ReturnType<typeof vi.fn<(answers: unknown, revision: number) => Promise<QuizAttempt>>>;
    submit: ReturnType<typeof vi.fn<(answers: unknown, revision: number) => Promise<QuizResult>>>;
  };
  let session: QuizSession;
  beforeEach(() => {
    vi.useFakeTimers();
    now = 0;
    transport = {
      read: vi.fn().mockResolvedValue(attempt()),
      save: vi.fn().mockResolvedValue(attempt({ revision: 1 })),
      submit: vi.fn().mockResolvedValue(result),
    };
    session = new QuizSession(attempt(), transport, () => now);
  });
  afterEach(() => {
    session.stop();
    vi.useRealTimers();
  });
  const advance = async (ms: number) => {
    now += ms;
    await vi.advanceTimersByTimeAsync(ms);
  };

  it("writes changed answers at 10 seconds and resumes accepted answers with the original deadline", async () => {
    transport.save.mockResolvedValue(
      attempt({
        revision: 1,
        server_now: "2026-10-08T00:00:10Z",
        questions: [
          { ...attempt().questions[0]!, saved_answer: { text: "accepted", option_ids: [] } },
        ],
      }),
    );
    session.start();
    session.edit("q", { text: "accepted" });
    await advance(9999);
    expect(transport.save).not.toHaveBeenCalled();
    await advance(1);
    expect(transport.save).toHaveBeenCalledExactlyOnceWith(
      [{ question_id: "q", answer: { text: "accepted", option_ids: [] } }],
      0,
    );
    expect(session.getSnapshot()).toMatchObject({ phase: "saved", dirty: 0, remaining: 50 });
    const resumed = new QuizSession(session.getSnapshot().attempt, transport, () => now);
    expect(resumed.getSnapshot().answers.q?.text).toBe("accepted");
    expect(resumed.getSnapshot().attempt.expires_at).toBe(attempt().expires_at);
  });

  it("retains newer edits made during a save and serializes writes with the returned revision", async () => {
    const first = deferred<QuizAttempt>();
    transport.save.mockReturnValueOnce(first.promise);
    session.edit("q", { text: "first" });
    const saving = session.save();
    await Promise.resolve();
    session.edit("q", { text: "second" });
    const next = session.save();
    expect(transport.save).toHaveBeenCalledTimes(1);
    first.resolve(attempt({ revision: 1 }));
    await saving;
    await next;
    expect(transport.save.mock.calls[0]).toEqual([
      [{ question_id: "q", answer: { text: "first", option_ids: [] } }],
      0,
    ]);
    expect(transport.save.mock.calls[1]).toEqual([
      [{ question_id: "q", answer: { text: "second", option_ids: [] } }],
      1,
    ]);
    expect(session.getSnapshot().dirty).toBe(0);
  });

  it("keeps unsaved work after a network failure and retries the same revision", async () => {
    transport.save.mockRejectedValueOnce(new Error("offline"));
    session.edit("q", { text: "keep me" });
    await expect(session.save()).rejects.toThrow("offline");
    expect(session.getSnapshot()).toMatchObject({
      dirty: 1,
      phase: "error",
      answers: { q: { text: "keep me" } },
    });
    await session.save();
    expect(transport.save.mock.calls.map((call) => call[1])).toEqual([0, 0]);
    expect(session.getSnapshot().phase).toBe("saved");
  });

  it("does not overwrite another device after a 409; discard requires an explicit refresh", async () => {
    transport.save.mockRejectedValueOnce(conflict());
    transport.read.mockResolvedValue(
      attempt({
        revision: 2,
        questions: [{ ...attempt().questions[0]!, saved_answer: { text: "other device" } }],
      }),
    );
    session.edit("q", { text: "local" });
    await expect(session.save()).rejects.toBeInstanceOf(ApiError);
    await session.save();
    await session.submit();
    session.edit("q", { text: "overwrite" });
    expect(transport.save).toHaveBeenCalledTimes(1);
    expect(transport.submit).not.toHaveBeenCalled();
    expect(session.getSnapshot().answers.q?.text).toBe("local");
    await session.refresh(true);
    expect(session.getSnapshot()).toMatchObject({
      dirty: 0,
      phase: "ready",
      attempt: { revision: 2 },
    });
    expect(session.getSnapshot().answers.q?.text).toBe("other device");
  });

  it("detects another device during a passive refresh without losing local work", async () => {
    session.edit("q", { text: "local" });
    transport.read.mockResolvedValue(attempt({ revision: 3 }));
    await session.refresh();
    expect(session.getSnapshot()).toMatchObject({
      phase: "conflict",
      dirty: 1,
      attempt: { revision: 0 },
    });
  });

  it("uses monotonic elapsed time even if the browser wall clock changes", async () => {
    session.start();
    vi.setSystemTime(new Date("2099-01-01"));
    await advance(5000);
    expect(session.getSnapshot().remaining).toBe(55);
    vi.setSystemTime(new Date("2000-01-01"));
    await advance(5000);
    expect(session.getSnapshot().remaining).toBe(50);
  });

  it("at expiry refreshes the server revision and submits no late local answers", async () => {
    session.edit("q", { text: "late unacknowledged answer" });
    transport.read.mockResolvedValue(attempt({ revision: 4, server_now: attempt().expires_at }));
    now = 60_000;
    await session.submit(true);
    expect(transport.read).toHaveBeenCalledOnce();
    expect(transport.submit).toHaveBeenCalledExactlyOnceWith([], 4);
    expect(session.getSnapshot()).toMatchObject({ phase: "submitted", dirty: 0, result });
    session.edit("q", { text: "too late" });
    expect(session.getSnapshot().dirty).toBe(0);
  });

  it("manual submission waits for an in-flight save and uses its new revision", async () => {
    const saved = deferred<QuizAttempt>();
    transport.save.mockReturnValueOnce(saved.promise);
    session.edit("q", { text: "accepted" });
    const saving = session.save();
    await Promise.resolve();
    const submitted = session.submit();
    expect(transport.submit).not.toHaveBeenCalled();
    saved.resolve(attempt({ revision: 7 }));
    await saving;
    await submitted;
    expect(transport.submit).toHaveBeenCalledExactlyOnceWith([], 7);
    expect(session.getSnapshot().phase).toBe("submitted");
  });

  it("recognizes sweeper completion without trying to submit it again", async () => {
    transport.read.mockResolvedValue(attempt({ state: "submitted", score: "1", passed: true }));
    await session.submit(true);
    expect(transport.submit).not.toHaveBeenCalled();
    expect(session.getSnapshot().phase).toBe("submitted");
  });

  it("backs off automatic expiry retries and clears timers on stop", async () => {
    transport.submit.mockRejectedValue(new Error("offline"));
    transport.read.mockResolvedValue(attempt({ server_now: attempt().expires_at }));
    now = 60_000;
    session.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(transport.submit).toHaveBeenCalledTimes(1);
    await advance(4000);
    expect(transport.submit).toHaveBeenCalledTimes(1);
    await advance(1000);
    expect(transport.submit).toHaveBeenCalledTimes(2);
    session.stop();
    await advance(20_000);
    expect(transport.submit).toHaveBeenCalledTimes(2);
  });

  it("clears local work and stops writing if access is revoked", async () => {
    transport.save.mockRejectedValue(
      new ApiError({ status: 404, code: "not_found", message: "Hidden" }),
    );
    session.edit("q", { text: "private" });
    await expect(session.save()).rejects.toBeInstanceOf(ApiError);
    expect(session.getSnapshot()).toMatchObject({ phase: "denied", answers: {}, dirty: 0 });
    await session.save();
    await session.submit();
    expect(transport.save).toHaveBeenCalledTimes(1);
    expect(transport.submit).not.toHaveBeenCalled();
  });
});
