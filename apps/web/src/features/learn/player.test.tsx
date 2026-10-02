import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type * as ApiModule from "./api";
import { learnKeys } from "./api";
import { CoursePlayer } from "./course-player";
import { Dashboard } from "./dashboard";
import type { Enrollment, EnrollmentDetail } from "./outline";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
// Recording a visit and the video player talk to the network; neither matters here.
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof ApiModule>()),
  useVisitLesson: () => ({ mutate: vi.fn() }),
}));
vi.mock("@/features/video/enrollment-video", () => ({
  EnrollmentVideo: () => <div>video player</div>,
}));

function withData(seed: (qc: QueryClient) => void, ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  seed(qc);
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const enrollment = (over: Partial<Enrollment>): Enrollment => ({
  id: "e1",
  course_id: "c1",
  course_title: "DSA Basics",
  status: "active",
  major_version: 1,
  version: "1.0",
  progress_percent: 0,
  enrolled_at: "2026-09-01T00:00:00Z",
  last_accessed_at: null,
  last_lesson_id: null,
  completed_at: null,
  ...over,
});

describe("Dashboard", () => {
  it("shows Continue learning first, with progress", () => {
    withData(
      (qc) =>
        qc.setQueryData(learnKeys.enrollments, [
          enrollment({ id: "new", course_title: "Graphs" }),
          enrollment({
            id: "started",
            course_title: "Arrays",
            progress_percent: 40,
            last_accessed_at: "2026-09-30T00:00:00Z",
          }),
        ]),
      <Dashboard />,
    );
    const continueLearning = screen.getByRole("region", { name: "Continue learning" });
    expect(within(continueLearning).getByText("Arrays")).toBeInTheDocument();
    expect(within(continueLearning).getByText("40% complete")).toBeInTheDocument();
    expect(within(continueLearning).getByRole("link")).toHaveAttribute(
      "href",
      "/learn/enrollments/started",
    );
    expect(
      within(screen.getByRole("region", { name: "My courses" })).getByText("Graphs"),
    ).toBeInTheDocument();
  });

  it("explains an empty dashboard", () => {
    withData((qc) => qc.setQueryData(learnKeys.enrollments, []), <Dashboard />);
    expect(screen.getByText(/assigns them to your batch/)).toBeInTheDocument();
  });
});

const lesson = (id: string, type: string, content: Record<string, unknown> = {}) => ({
  id,
  title: `Lesson ${id}`,
  lesson_type: type,
  position: 1,
  is_required: true,
  completion_threshold: type === "video" ? "0.90" : null,
  estimated_minutes: null,
  video_duration_seconds: type === "video" ? 120 : null,
  content,
});

const detail: EnrollmentDetail = {
  enrollment: enrollment({ progress_percent: 33, last_lesson_id: "n" }),
  version: { id: "v1", major: 1, minor: 0, version: "1.0", title: "DSA Basics" },
  outline: {
    schema: 1,
    course: { id: "c1", title: "DSA Basics", description: "" },
    modules: [
      {
        id: "m1",
        title: "Arrays",
        position: 1,
        lessons: [
          lesson("v", "video", { video_asset_id: "a1" }),
          lesson("n", "notes", {
            html: "<h2>Two pointers</h2><p>Walk inward.</p>",
            image_file_ids: [],
          }),
          lesson("p", "pdf", { file_id: "f1" }),
        ],
      },
    ],
  },
  progress: [
    {
      lesson_id: "v",
      status: "completed",
      completed_at: "2026-09-30T00:00:00Z",
      pdf_opened_at: null,
      video_position_seconds: 120,
      watched_ratio: "1.0",
    },
  ],
};

describe("CoursePlayer", () => {
  it("renders the lesson, completion ticks, progress and navigation", () => {
    withData(
      (qc) => qc.setQueryData(learnKeys.enrollment("e1"), detail),
      <CoursePlayer enrollmentId="e1" lessonId="n" />,
    );
    expect(screen.getByText("33% complete")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Two pointers" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark complete" })).toBeEnabled();

    const outlines = screen.getAllByRole("list", { name: "Course outline" });
    const outline = outlines[0]!;
    expect(
      within(outline).getByRole("link", { name: /Lesson v.*\(completed\)/ }),
    ).toBeInTheDocument();
    expect(within(outline).getByRole("link", { name: /Lesson n/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "← Previous" })).toHaveAttribute(
      "href",
      "/learn/enrollments/e1/lessons/v",
    );
    expect(screen.getByRole("link", { name: "Next →" })).toHaveAttribute(
      "href",
      "/learn/enrollments/e1/lessons/p",
    );
  });

  it("asks for the PDF to be opened before it can be completed", () => {
    withData(
      (qc) => qc.setQueryData(learnKeys.enrollment("e1"), detail),
      <CoursePlayer enrollmentId="e1" lessonId="p" />,
    );
    expect(screen.getByRole("button", { name: "Open the PDF" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark complete" })).toBeDisabled();
    expect(screen.getByText("Open the PDF first, then mark it complete.")).toBeInTheDocument();
  });

  it("explains a lesson that isn't in the student's version", () => {
    withData(
      (qc) => qc.setQueryData(learnKeys.enrollment("e1"), detail),
      <CoursePlayer enrollmentId="e1" lessonId="gone" />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("isn't in your version");
  });
});
