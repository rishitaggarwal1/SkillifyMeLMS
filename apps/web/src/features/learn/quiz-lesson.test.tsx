import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { QuizQuestion, QuizResult } from "./quiz-api";
import { QuizQuestionField, QuizSolutions } from "./quiz-lesson";

const question: QuizQuestion = {
  id: "q",
  question_type: "mcq_single",
  prompt: "Choose a language",
  marks: "2",
  options: [
    { id: "a", text: "Python" },
    { id: "b", text: "CSS" },
  ],
  skill_ids: [],
  saved_answer: null,
};
const full: QuizResult = {
  attempt_id: "a1",
  reveal_mode: "explanations",
  score: "2",
  max_marks: "2",
  pass_marks: "1",
  passed: true,
  context: {
    state: "submitted",
    configured_reveal_mode: "explanations",
    reveal_timing: "immediately",
    attempts_allowed: 2,
    attempts_used: 1,
    has_active_attempt: false,
  },
  solutions: [
    {
      question_id: "q",
      answer_key: { correct_option_ids: ["a"], accepted_answers: [], case_sensitive: false },
      explanation: "Private explanation",
    },
  ],
};

describe("student question controls", () => {
  it("selects a single stable option id without showing an answer key", () => {
    const onChange = vi.fn();
    render(
      <QuizQuestionField
        question={question}
        index={0}
        answer={{}}
        disabled={false}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Python" }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith({ text: null, option_ids: ["a"] });
    expect(screen.queryByText("Private explanation")).not.toBeInTheDocument();
  });
  it("adds and removes independent multi-select option ids", () => {
    const onChange = vi.fn();
    render(
      <QuizQuestionField
        question={{ ...question, question_type: "mcq_multi" }}
        index={1}
        answer={{ option_ids: ["a"] }}
        disabled={false}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "CSS" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Python" }));
    expect(onChange.mock.calls).toEqual([
      [{ text: null, option_ids: ["a", "b"] }],
      [{ text: null, option_ids: [] }],
    ]);
  });
  it("keeps entered fill-blank text and disables all inputs at the deadline", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <QuizQuestionField
        question={{ ...question, question_type: "fill_blank" }}
        index={2}
        answer={{ text: "prior" }}
        disabled={false}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByLabelText("Answer to question 3"), {
      target: { value: "  Python  " },
    });
    expect(onChange).toHaveBeenCalledExactlyOnceWith({ text: "  Python  ", option_ids: [] });
    rerender(
      <QuizQuestionField
        question={{ ...question, question_type: "fill_blank" }}
        index={2}
        answer={{ text: "prior" }}
        disabled={true}
        onChange={onChange}
      />,
    );
    expect(screen.getByLabelText("Answer to question 3")).toBeDisabled();
  });
});
describe("result reveal defense in depth", () => {
  const show = (result: QuizResult) =>
    render(<QuizSolutions result={result} questions={[question]} />);
  it("reveals only permitted correct answers and explanations after submission", () => {
    show(full);
    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.getByText("Private explanation")).toBeInTheDocument();
  });
  it("hides supplied keys when the configured mode is score_only", () => {
    show({ ...full, context: { ...full.context, configured_reveal_mode: "score_only" } });
    expect(screen.queryByRole("region", { name: "Correct answers" })).not.toBeInTheDocument();
  });
  it("hides supplied keys when the result projection is score_only", () => {
    show({ ...full, reveal_mode: "score_only" } as QuizResult);
    expect(screen.queryByText("Python")).not.toBeInTheDocument();
  });
  it("hides keys until the allowance is exhausted", () => {
    show({ ...full, context: { ...full.context, reveal_timing: "after_attempts_exhausted" } });
    expect(screen.queryByText("Private explanation")).not.toBeInTheDocument();
  });
  it("hides keys while an attempt is active, even when the allowance is used", () => {
    show({
      ...full,
      context: {
        ...full.context,
        reveal_timing: "after_attempts_exhausted",
        attempts_used: 2,
        has_active_attempt: true,
      },
    });
    expect(screen.queryByText("Python")).not.toBeInTheDocument();
  });
  it("reveals keys after exhaustion with no active attempt", () => {
    show({
      ...full,
      context: { ...full.context, reveal_timing: "after_attempts_exhausted", attempts_used: 2 },
    });
    expect(screen.getByText("Python")).toBeInTheDocument();
  });
  it("does not render explanation fields when the configured mode only allows answers", () => {
    show({ ...full, context: { ...full.context, configured_reveal_mode: "correct_answers" } });
    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.queryByText("Private explanation")).not.toBeInTheDocument();
  });
  it("does not reveal a malformed result without submitted state", () => {
    show({ ...full, context: { ...full.context, state: "in_progress" } } as unknown as QuizResult);
    expect(screen.queryByText("Python")).not.toBeInTheDocument();
  });
});
