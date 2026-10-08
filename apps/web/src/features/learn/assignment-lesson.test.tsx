import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AssignmentGrade, StudentAssignment, SubmissionAttempt } from "@/lib/api/types";
import { myAssignmentQuery } from "./api";
import { AssignmentLesson } from "./assignment-lesson";
import type { OutlineLesson } from "./outline";

const { get, put } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get, PUT: put } }));
const lesson: OutlineLesson = {
  id: "lesson",
  title: "Homework",
  lesson_type: "assignment",
  position: 1,
  is_required: true,
  content: {},
  completion_threshold: null,
  estimated_minutes: null,
  video_duration_seconds: null,
};
const initial: StudentAssignment = {
  assignment: {
    assignment_id: "definition",
    title: "Homework",
    instructions_html: "<p>Published instructions</p>",
    max_marks: 10,
    due_at: "2026-10-07T00:00:00Z",
    submission_kinds: ["text", "file"],
    rubric: {
      criteria: [{ id: "quality", label: "Edited label", description: "", max_marks: "10" }],
    },
    late_policy: { mode: "penalty", percent_per_day: "10" },
    image_file_ids: [],
  },
  submission: null,
  server_time: "2026-10-08T00:00:00Z",
  late: {
    due_at: "2026-10-07T00:00:00Z",
    policy: { mode: "penalty", percent_per_day: "10" },
    is_late: true,
    late_days: 1,
    penalty_percent: "10",
    closed: false,
  },
};
const grade: AssignmentGrade = {
  id: "grade",
  attempt_id: "attempt2",
  grade_sequence: 2,
  score: "7.2",
  raw_score: "8",
  max_marks: 10,
  feedback: "Good work",
  graded_at: "2026-10-08T00:00:00Z",
  graded_by: null,
  penalty_percent: "10",
  penalty_marks: "0.8",
  rubric_breakdown: [{ criterion_id: "quality", score: "8" }],
};
const attempt = (id: string, n: number): SubmissionAttempt => ({
  id,
  attempt_number: n,
  submission_id: "submission",
  version_id: "v",
  submitted_at: "2026-10-07T12:00:00Z",
  is_active: n === 2,
  kind: "text",
  text_body: "Work " + n,
  file: null,
  late: initial.late ?? null,
  grade: n === 2 ? grade : null,
  assignment: {
    ...initial.assignment,
    instructions_html: "<p>Frozen instructions " + n + "</p>",
    rubric: {
      criteria: [{ id: "quality", label: "Frozen quality", max_marks: "10", description: "" }],
    },
  },
  image_urls: {},
});
const ok = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) });

describe("student assignment", () => {
  let data: StudentAssignment;
  let client: QueryClient;
  beforeEach(() => {
    data = structuredClone(initial);
    client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    get
      .mockReset()
      .mockImplementation(
        (
          path: string,
          options?: { params?: { path?: { attempt_id?: string }; query?: { cursor?: string } } },
        ) =>
          Promise.resolve(
            ok(
              path.endsWith("/assignment")
                ? data
                : path.endsWith("/submission-attempts")
                  ? { items: [attempt("attempt2", 2), attempt("attempt1", 1)], next_cursor: null }
                  : path.endsWith("/grades")
                    ? options?.params?.path?.attempt_id === "attempt1"
                      ? { items: [], next_cursor: null }
                      : options?.params?.query?.cursor === "older-grade"
                        ? {
                            items: [{ ...grade, id: "old-grade", grade_sequence: 1, score: "6.3" }],
                            next_cursor: null,
                          }
                        : { items: [grade], next_cursor: "older-grade" }
                    : { urls: {}, expires_at: null },
            ),
          ),
      );
    put.mockReset();
  });
  afterEach(() => client.clear());
  const show = () =>
    render(
      <QueryClientProvider client={client}>
        <AssignmentLesson enrollmentId="e" lesson={lesson} />
      </QueryClientProvider>,
    );

  it("shows the server-calculated late penalty before submission and retains work on failure", async () => {
    put.mockRejectedValue(new Error("Connection lost"));
    show();
    const preview = await screen.findByRole("region", { name: "Before submitting" });
    expect(preview).toHaveTextContent("1 begun day late · 10% penalty");
    const answer = screen.getByLabelText("Your answer");
    fireEvent.change(answer, { target: { value: "Keep this work" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection lost");
    expect(answer).toHaveValue("Keep this work");
    expect(put).toHaveBeenCalledWith(
      "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/submission",
      {
        params: { path: { enrollment_id: "e", lesson_id: "lesson" }, header: { "If-Match": "0" } },
        body: { submission: { kind: "text", text: "Keep this work" } },
      },
    );
  });

  it("disables submission when server status becomes closed, retaining entered work", async () => {
    show();
    const answer = await screen.findByLabelText("Your answer");
    fireEvent.change(answer, { target: { value: "Before closing" } });
    data = { ...data, late: { ...data.late!, closed: true, policy: { mode: "reject" } } };
    act(() => client.setQueryData(myAssignmentQuery("e", "lesson").queryKey, data));
    expect(answer).toHaveValue("Before closing");
    await waitFor(() => expect(answer).toBeDisabled());
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
    expect(put).not.toHaveBeenCalled();
  });

  it("retains unsaved text when a background status refresh fails", async () => {
    show();
    const answer = await screen.findByLabelText("Your answer");
    fireEvent.change(answer, { target: { value: "Keep through a failed read" } });
    get.mockRejectedValueOnce(new Error("Status refresh unavailable"));
    await act(async () => {
      await client.refetchQueries({ queryKey: myAssignmentQuery("e", "lesson").queryKey });
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Status refresh unavailable");
    expect(screen.getByLabelText("Your answer")).toBe(answer);
    expect(answer).toHaveValue("Keep through a failed read");
    expect(screen.getByRole("button", { name: "Submit" })).toBeEnabled();
  });

  it("shows frozen rubric, raw marks, accepted penalty and final score after grading", async () => {
    data = {
      ...data,
      submission: {
        id: "submission",
        kind: "text",
        text_body: "Graded work",
        file: null,
        status: "graded",
        revision: 3,
        submitted_at: "2026-10-07T12:00:00Z",
        grade,
        late: initial.late,
        active_attempt_id: "attempt2",
        attempt_number: 2,
      },
    };
    const invalidation = vi.spyOn(client, "invalidateQueries");
    show();
    const result = await screen.findByRole("region", { name: "Your grade" });
    await waitFor(() => expect(result).toHaveTextContent("Frozen quality"));
    expect(result).toHaveTextContent("Earned marks8 / 10");
    expect(result).toHaveTextContent("Late penalty (10%)−0.8");
    expect(result).toHaveTextContent("Final score7.2 / 10");
    expect(result).not.toHaveTextContent("Edited label");
    expect(screen.queryByRole("button", { name: "Submit" })).not.toBeInTheDocument();
    expect(invalidation).toHaveBeenCalledWith({ queryKey: ["learn", "enrollments", "e"] });
  });

  it("keeps attempt history read-only and cursor-loads separate grade history", async () => {
    data = {
      ...data,
      submission: {
        id: "submission",
        kind: "text",
        text_body: "Second work",
        file: null,
        status: "submitted",
        revision: 2,
        submitted_at: "2026-10-07T12:00:00Z",
        grade: null,
        late: initial.late,
        active_attempt_id: "attempt2",
        attempt_number: 2,
      },
    };
    show();
    await screen.findByLabelText("Your answer");
    await userEvent.click(screen.getByText("Submission and grade history"));
    const history = await screen.findByRole("region", { name: "Submission attempt history" });
    const grades = await within(history).findByRole("region", { name: "Grade history" });
    await userEvent.click(await within(grades).findByRole("button", { name: "Load more" }));
    await waitFor(() => expect(within(grades).getAllByRole("listitem")).toHaveLength(2));
    await userEvent.click(within(history).getByRole("button", { name: /^Attempt 1/ }));
    expect(within(history).getByRole("region", { name: "Pinned instructions" })).toHaveTextContent(
      "Frozen instructions 1",
    );
    await within(history).findByText("This attempt has not been graded.");
    expect(within(history).queryByRole("textbox")).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(
      "/api/v1/enrollments/{enrollment_id}/lessons/{lesson_id}/submission-attempts/{attempt_id}/grades",
      {
        params: {
          path: { enrollment_id: "e", lesson_id: "lesson", attempt_id: "attempt1" },
          query: { limit: 25, cursor: undefined },
        },
      },
    );
  });
});
