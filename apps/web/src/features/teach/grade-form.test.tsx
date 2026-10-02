import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import type { GraderSubmission } from "@/lib/api/types";

import { GradeForm, gradeSchema } from "./grade-form";

function submission(overrides: Partial<GraderSubmission["submission"]> = {}): GraderSubmission {
  return {
    id: "s1",
    course_id: "c1",
    lesson_id: "l1",
    student: { id: "u1", full_name: "Aarav Sharma", email: "aarav@college.test" },
    assignment: {
      assignment_id: "a1",
      title: "FizzBuzz",
      instructions_html: "<p>Solve.</p>",
      due_at: null,
      max_marks: 10,
      submission_kinds: ["text"],
    },
    submission: {
      id: "s1",
      kind: "text",
      text_body: "print(1)",
      file: null,
      status: "submitted",
      revision: 3,
      submitted_at: "2026-10-02T10:00:00Z",
      grade: null,
      ...overrides,
    },
  };
}

async function fill(score: string, feedback = "") {
  const scoreBox = screen.getByLabelText("Score (out of 10)");
  await userEvent.clear(scoreBox);
  await userEvent.type(scoreBox, score);
  if (feedback) await userEvent.type(screen.getByLabelText(/Feedback/), feedback);
  await userEvent.click(screen.getByRole("button", { name: /grade/i }));
}

describe("gradeSchema", () => {
  it.each([
    ["0", true],
    ["10", true],
    ["7.5", true],
    ["8.25", true],
    ["10.5", false],
    ["-1", false],
    ["8.125", false],
    ["eight", false],
    ["", false],
  ])("score %s -> valid %s", (score, valid) => {
    expect(gradeSchema(10).safeParse({ score, feedback: "" }).success).toBe(valid);
  });
});

describe("<GradeForm />", () => {
  it("sends the score, feedback and the submission revision it was opened at", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const onSaved = vi.fn();
    render(
      <GradeForm submission={submission()} save={save} onConflict={vi.fn()} onSaved={onSaved} />,
    );
    await fill("8.5", "Nice loop");
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({ score: "8.5", feedback: "Nice loop", revision: 3 }),
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it("refuses scores above the maximum before calling the API", async () => {
    const save = vi.fn();
    render(<GradeForm submission={submission()} save={save} onConflict={vi.fn()} />);
    await fill("11");
    expect(await screen.findByText("At most 10.")).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("explains a conflict and asks for the latest submission", async () => {
    const conflict = new ApiError({
      status: 409,
      code: "revision_conflict",
      message: "The submission changed since you opened it.",
    });
    const save = vi.fn().mockRejectedValue(conflict);
    const onConflict = vi.fn();
    render(<GradeForm submission={submission()} save={save} onConflict={onConflict} />);
    await fill("6");
    expect(await screen.findByRole("alert")).toHaveTextContent(/changed since you opened it/);
    expect(onConflict).toHaveBeenCalled();
  });

  it("shows other API errors", async () => {
    const save = vi.fn().mockRejectedValue(
      new ApiError({
        status: 422,
        code: "score_out_of_range",
        message: "The score can't be more than 10.",
      }),
    );
    render(<GradeForm submission={submission()} save={save} onConflict={vi.fn()} />);
    await fill("9");
    expect(await screen.findByRole("alert")).toHaveTextContent("The score can't be more than 10.");
  });

  it("starts from the existing grade when correcting it", () => {
    render(
      <GradeForm
        submission={submission({
          status: "graded",
          grade: {
            score: "8.50",
            max_marks: 10,
            feedback: "Good",
            graded_at: "2026-10-02T11:00:00Z",
            graded_by: "g1",
          },
        })}
        save={vi.fn()}
        onConflict={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Score (out of 10)")).toHaveValue("8.5");
    expect(screen.getByRole("button", { name: "Update grade" })).toBeInTheDocument();
  });
});
