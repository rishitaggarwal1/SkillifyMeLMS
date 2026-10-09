import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { toast, toastQueue } from "@/lib/toast";
import { Toaster } from "./lazy-toaster";

afterEach(() => vi.unstubAllGlobals());

it("mounts the real themed toast renderer on first use and shows the first queued message once", async () => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  );
  expect(toastQueue.getSnapshot().runtime).toBeNull();
  act(() => toast.success("First saved notification"));
  const view = render(<Toaster />);
  await act(async () => {
    await vi.dynamicImportSettled();
  });
  await waitFor(() => expect(screen.getAllByText("First saved notification")).toHaveLength(1));
  expect(view.container.querySelector("[data-sonner-toaster]")).toHaveAttribute(
    "data-sonner-theme",
    "light",
  );
  act(() => toast.info("Second notification"));
  await waitFor(() => expect(screen.getAllByText("Second notification")).toHaveLength(1));
});

it("keeps messages accessible with retry and dismiss controls if a chunk load fails", () => {
  const snapshot = {
    runtime: null,
    failed: true,
    messages: [{ id: 7, kind: "error" as const, message: "Upload failed; try again" }],
  };
  vi.spyOn(toastQueue, "getSnapshot").mockReturnValue(snapshot);
  vi.spyOn(toastQueue, "subscribe").mockImplementation(() => () => {});
  const retry = vi.spyOn(toastQueue, "retry").mockImplementation(() => {});
  const dismiss = vi.spyOn(toastQueue, "dismiss").mockImplementation(() => {});
  render(<Toaster />);
  expect(screen.getByRole("alert")).toHaveTextContent("Upload failed; try again");
  const button = screen.getByRole("button", { name: "Retry" });
  expect(button).toHaveClass("min-h-11");
  fireEvent.click(button);
  expect(retry).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  expect(dismiss).toHaveBeenCalledExactlyOnceWith(7);
});
