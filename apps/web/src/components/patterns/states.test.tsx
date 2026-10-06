import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ErrorAlert } from "./page";
import { FirstRunChecklist, FormField, PageSkeleton } from "./states";

describe("shared states", () => {
  it("ticks completed steps, acts on the first incomplete step and disappears when done", async () => {
    const action = vi.fn();
    const steps = [
      { label: "Create a course", done: true, href: "/teach/courses" },
      { label: "Add a lesson", done: false, href: "/teach/courses", onAction: action },
      { label: "Publish", done: false, href: "/teach/courses" },
    ];
    const view = render(<FirstRunChecklist title="First course" steps={steps} />);
    expect(screen.getByText("Completed:")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add a lesson" }));
    expect(action).toHaveBeenCalledOnce();
    view.rerender(
      <FirstRunChecklist title="First course" steps={steps.map((s) => ({ ...s, done: true }))} />,
    );
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
  it("connects a field's label, help and error and disables while saving", () => {
    render(
      <FormField label="Title" help="Visible to students" error="Required" saving>
        {(props) => <input {...props} />}
      </FormField>,
    );
    const field = screen.getByRole("textbox", { name: "Title" });
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAccessibleDescription("Visible to students Required");
    expect(field).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Required");
    expect(screen.getByRole("status")).toHaveTextContent("Saving");
  });
  it("retries the failed operation without implying success", async () => {
    const retry = vi.fn();
    render(<ErrorAlert error={new Error("Offline")} onRetry={retry} />);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.getByRole("alert")).toHaveTextContent("Offline");
  });
  it("announces loading without displaying fake counts", () => {
    render(<PageSkeleton kind="table" rows={2} />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});
