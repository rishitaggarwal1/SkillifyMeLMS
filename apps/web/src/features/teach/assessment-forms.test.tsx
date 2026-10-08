import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import type { AuthorQuestion, GraderSubmission, Rubric } from "@/lib/api/types";
import { cents, gradePreview } from "@/lib/marks";
import {
  assignmentFields,
  assignmentFormSchema,
  type AssignmentFormValues,
} from "./assignment-editor";
import { GradeForm, rubricGradeSchema } from "./grade-form";
import {
  QuestionForm,
  questionFields,
  questionFormSchema,
  type QuestionFormValues,
} from "./question-form";
import { quizFields, quizFormSchema, type QuizFormValues } from "./quiz-editor";

const question: QuestionFormValues = {
  question_type: "mcq_single",
  prompt: "Choose the language",
  options: [
    { id: "python", text: "Python", correct: true },
    { id: "css", text: "CSS", correct: false },
  ],
  accepted: "",
  case_sensitive: false,
  explanation: "Python is a programming language.",
};
const rubric: Rubric = {
  criteria: [
    { id: "correctness", label: "Correctness", max_marks: "6", description: "Correct output" },
    { id: "clarity", label: "Clarity", max_marks: "4", description: "" },
  ],
};
const assignment: AssignmentFormValues = {
  title: "Homework",
  due: "2026-10-07T10:00",
  max_marks: "10",
  submission_kinds: ["text"],
  rubric_enabled: true,
  criteria: rubric.criteria.map((c) => ({ ...c, description: c.description ?? "" })),
  late_mode: "penalty",
  penalty_rate: "10",
};
const quiz: QuizFormValues = {
  title: "Quiz",
  mode: "manual",
  bank_id: "bank",
  questions: [{ question_id: "q1", marks: "2.50" }],
  draw_count: "1",
  marks_per_question: "1",
  skill_ids: [],
  pass_marks: "2",
  time_limit_seconds: "600",
  attempts_allowed: "2",
  randomize_order: true,
  reveal_mode: "explanations",
  reveal_timing: "after_attempts_exhausted",
};

describe("author validation", () => {
  it("preserves option identity while editing text and keys", () => {
    expect(questionFormSchema.safeParse(question).success).toBe(true);
    expect(
      questionFields({
        ...question,
        options: question.options.map((o) => ({ ...o, text: o.text + " edited" })),
      }).options?.map((o) => o.id),
    ).toEqual(["python", "css"]);
  });
  it.each([
    { options: question.options.map((o) => ({ ...o, correct: true })) },
    { options: question.options.map((o) => ({ ...o, id: "duplicate" })) },
    { prompt: " " },
    {
      question_type: "mcq_multi" as const,
      options: question.options.map((o) => ({ ...o, correct: true })),
    },
  ])("rejects invalid choices %j", (patch) => {
    expect(questionFormSchema.safeParse({ ...question, ...patch }).success).toBe(false);
  });
  it("normalizes fill-blank text and ignores unused options", () => {
    const fields = {
      ...question,
      question_type: "fill_blank" as const,
      accepted: "  Cafe\u0301 \nPython\n",
    };
    expect(questionFormSchema.safeParse(fields).success).toBe(true);
    expect(questionFields(fields).answer_key).toEqual({
      correct_option_ids: [],
      accepted_answers: ["Café", "Python"],
      case_sensitive: false,
    });
    expect(questionFields(fields).options).toEqual([]);
    expect(questionFormSchema.safeParse({ ...fields, accepted: "Python\nPYTHON" }).success).toBe(
      false,
    );
    expect(
      questionFormSchema.safeParse({ ...fields, accepted: "Python\nPYTHON", case_sensitive: true })
        .success,
    ).toBe(true);
  });
  it.each([
    { criteria: [{ ...assignment.criteria[0]!, max_marks: "10.01" }] },
    { criteria: assignment.criteria.map((c) => ({ ...c, id: "duplicate" })) },
    { criteria: [{ ...assignment.criteria[0]!, max_marks: "0" }] },
    { criteria: [] },
    { due: "" },
    { penalty_rate: "100.01" },
    { penalty_rate: "0" },
  ])("rejects invalid rubric or penalty %j", (patch) => {
    expect(assignmentFormSchema.safeParse({ ...assignment, ...patch }).success).toBe(false);
  });
  it("keeps criterion IDs and allows accepting/rejecting without a due date", () => {
    expect(assignmentFormSchema.safeParse(assignment).success).toBe(true);
    expect(assignmentFields(assignment, null).rubric?.criteria.map((c) => c.id)).toEqual([
      "correctness",
      "clarity",
    ]);
    for (const mode of ["accept", "reject"] as const) {
      const values = { ...assignment, due: "", late_mode: mode, penalty_rate: "unused" };
      expect(assignmentFormSchema.safeParse(values).success).toBe(true);
      expect(assignmentFields(values, null).late_policy).toEqual({ mode, percent_per_day: null });
    }
  });
  it.each([
    { questions: [] },
    { questions: [...quiz.questions, ...quiz.questions] },
    { pass_marks: "2.51" },
    { time_limit_seconds: "0" },
    { time_limit_seconds: "86401" },
    { attempts_allowed: "101" },
    { questions: [{ question_id: "q1", marks: "0.001" }] },
    { mode: "bank", bank_id: "", draw_count: "0" },
  ])("rejects invalid quiz rules %j", (patch) => {
    expect(quizFormSchema.safeParse({ ...quiz, ...patch }).success).toBe(false);
  });
  it("sends only the active selection mode and both reveal settings", () => {
    expect(quizFormSchema.safeParse(quiz).success).toBe(true);
    expect(quizFields(quiz).selection).toEqual({ mode: "manual", questions: quiz.questions });
    const bank = {
      ...quiz,
      mode: "bank" as const,
      draw_count: "3",
      marks_per_question: "1",
      skill_ids: ["skill"],
    };
    expect(quizFormSchema.safeParse(bank).success).toBe(true);
    expect(quizFields(bank)).toMatchObject({
      selection: {
        mode: "bank",
        bank_id: "bank",
        draw_count: 3,
        marks_per_question: "1",
        skill_ids: ["skill"],
      },
      reveal_mode: "explanations",
      reveal_timing: "after_attempts_exhausted",
    });
  });
});

describe("frozen grade previews and saves", () => {
  it.each([
    [["0.01"], "50", "0.01", "0.00"],
    [["6", "2.25"], "10", "0.83", "7.42"],
    [["6", "4"], "100", "10.00", "0.00"],
    [["0.10", "0.20"], "0", "0.00", "0.30"],
  ])("uses exact half-up rounding for %j at %s%%", (scores, percent, penalty, effective) => {
    expect(gradePreview(scores, percent)).toMatchObject({
      penalty_marks: penalty,
      score: effective,
    });
  });
  it("rejects malformed preview input and missing/over-limit rubric scores", () => {
    expect(cents("1.001")).toBeNull();
    expect(gradePreview([""], "10")).toBeNull();
    expect(gradePreview(["10"], "100.01")).toBeNull();
    for (const criteria of [["6"], ["6.01", "4"], ["6", "-1"]]) {
      expect(
        rubricGradeSchema(10, rubric).safeParse({ criteria, score: "", feedback: "" }).success,
      ).toBe(false);
    }
  });
  function detail(): GraderSubmission {
    return {
      id: "submission",
      course_id: "course",
      lesson_id: "lesson",
      student: { id: "student", full_name: "Student", email: "student@college.test" },
      assignment: {
        assignment_id: "assignment",
        title: "Homework",
        max_marks: 10,
        submission_kinds: ["text"],
        instructions_html: "<p>Solve</p>",
        due_at: null,
        rubric,
      },
      submission: {
        id: "submission",
        kind: "text",
        text_body: "work",
        file: null,
        revision: 7,
        status: "graded",
        submitted_at: "2026-10-07T10:00:00Z",
        active_attempt_id: "active",
        late: {
          due_at: "2026-10-06T10:00:00Z",
          policy: { mode: "penalty", percent_per_day: "10" },
          is_late: true,
          closed: false,
          late_days: 1,
          penalty_percent: "10",
        },
        grade: {
          id: "g1",
          score: "7.42",
          raw_score: "8.25",
          max_marks: 10,
          feedback: "First grade",
          graded_at: "2026-10-07T11:00:00Z",
          graded_by: "grader",
          rubric_breakdown: [
            { criterion_id: "correctness", score: "6" },
            { criterion_id: "clarity", score: "2.25" },
          ],
        },
      },
    };
  }
  it("regrades earned criterion scores, binds the active attempt and sends no effective score", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(<GradeForm submission={detail()} save={save} onConflict={vi.fn()} />);
    expect(screen.getByLabelText("Clarity (out of 4)")).toHaveValue("2.25");
    expect(screen.getByLabelText("Grade preview")).toHaveTextContent("7.42 / 10");
    await userEvent.click(screen.getByRole("button", { name: "Update grade" }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        criterion_scores: [
          { criterion_id: "correctness", score: "6" },
          { criterion_id: "clarity", score: "2.25" },
        ],
        feedback: "First grade",
        revision: 7,
        attempt_id: "active",
      }),
    );
  });
  it("keeps authored text after a bank conflict", async () => {
    const saved: AuthorQuestion = {
      id: "q1",
      bank_id: "bank",
      organization_id: "org",
      revision: 1,
      bank_revision: 1,
      archived_at: null,
      skill_ids: [],
      ...questionFields(question),
    } as AuthorQuestion;
    render(
      <QuestionForm
        question={saved}
        save={vi
          .fn()
          .mockRejectedValue(
            new ApiError({ status: 409, code: "revision_conflict", message: "stale" }),
          )}
      />,
    );
    const form = screen.getByRole("form", { name: "Question details" });
    await userEvent.clear(within(form).getByLabelText("Question", { exact: true }));
    await userEvent.type(
      within(form).getByLabelText("Question", { exact: true }),
      "My revised prompt",
    );
    await userEvent.click(within(form).getByRole("button", { name: "Save question" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your edits are kept");
    expect(within(form).getByLabelText("Question", { exact: true })).toHaveValue(
      "My revised prompt",
    );
  });
});
