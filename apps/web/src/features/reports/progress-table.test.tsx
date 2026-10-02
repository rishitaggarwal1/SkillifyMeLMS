import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProgressLesson, StudentProgress } from "@/lib/api/types";

import { ProgressGrid } from "./progress-table";

const lessons: ProgressLesson[] = [
  { id: "n1", title: "Variables", lesson_type: "notes", module_title: "Basics" },
  { id: "v1", title: "Loops video", lesson_type: "video", module_title: "Basics" },
  { id: "a1", title: "FizzBuzz", lesson_type: "assignment", module_title: "Practice" },
  { id: "x1", title: "New in v2", lesson_type: "notes", module_title: "Practice" },
];

function row(overrides: Partial<StudentProgress>): StudentProgress {
  return {
    student: { id: "u1", full_name: "Aarav Shah", email: "aarav@college.test" },
    enrollment_id: "e1",
    enrollment_status: "active",
    version: "1.0",
    progress_percent: 50,
    last_activity_at: "2026-10-02T05:30:00Z",
    completed_at: null,
    lessons: { n1: "completed", v1: "in_progress", a1: "not_started", x1: "not_in_version" },
    assignments: {},
    ...overrides,
  };
}

describe("<ProgressGrid />", () => {
  it("shows a row per student with per-lesson cells", () => {
    render(
      <ProgressGrid
        lessons={lessons}
        rows={[
          row({}),
          row({
            student: { id: "u2", full_name: "", email: "meera@college.test" },
            enrollment_id: null,
            enrollment_status: "not_enrolled",
            progress_percent: 0,
            last_activity_at: null,
            lessons: {},
          }),
        ]}
      />,
    );
    const [, aarav, meera] = screen.getAllByRole("row");
    const cells = within(aarav!).getAllByRole("cell");
    expect(within(aarav!).getByRole("rowheader")).toHaveTextContent("Aarav Shah");
    expect(cells[0]).toHaveTextContent("50%");
    expect(cells[1]).toHaveTextContent(/2 Oct 2026.*IST/);
    expect(cells[2]).toHaveTextContent("Completed");
    expect(cells[3]).toHaveTextContent("Started");
    expect(cells[4]).toHaveTextContent("Not started");
    expect(cells[5]).toHaveTextContent("n/a");
    // No name: the email stands in; not enrolled is said, and nothing was ever opened.
    expect(within(meera!).getByRole("rowheader")).toHaveTextContent("meera@college.test");
    expect(within(meera!).getByRole("rowheader")).toHaveTextContent("Not enrolled");
    expect(within(meera!).getAllByRole("cell")[1]).toHaveTextContent("Never");
  });

  it("shows assignment scores and pending submissions", () => {
    render(
      <ProgressGrid
        lessons={lessons}
        rows={[
          row({ assignments: { a1: { status: "graded", score: "8.50", max_marks: 10 } } }),
          row({
            student: { id: "u3", full_name: "Zoya Khan", email: "zoya@college.test" },
            assignments: { a1: { status: "submitted", score: null, max_marks: null } },
          }),
        ]}
      />,
    );
    const [, graded, pending] = screen.getAllByRole("row");
    expect(within(graded!).getAllByRole("cell")[4]).toHaveTextContent("8.5/10");
    expect(within(pending!).getAllByRole("cell")[4]).toHaveTextContent("Submitted");
  });

  it("names every column, with its module", () => {
    render(<ProgressGrid lessons={lessons} rows={[row({})]} />);
    expect(screen.getByRole("columnheader", { name: "FizzBuzz" })).toHaveAttribute(
      "title",
      "Practice / FizzBuzz",
    );
  });
});
