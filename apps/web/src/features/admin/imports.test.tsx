import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";
import type { ImportJob } from "@/lib/api/types";
import { renderWithQuery, testQueryClient } from "@/test/utils";

import { allBatchesQuery } from "./api";
import { JobSummary, UploadCard } from "./imports";
import { batchFormSchema } from "./batches";
import { inviteSchema } from "./members";
import { MAX_IMPORT_BYTES, importFileSchema } from "./upload";

const job = (overrides: Partial<ImportJob> = {}): ImportJob => ({
  id: "job-1",
  status: "running",
  file_name: "students.csv",
  batch_id: "b1",
  total_rows: 50,
  processed_rows: 20,
  created_count: 20,
  skipped_count: 0,
  error_count: 0,
  error_message: null,
  started_at: null,
  finished_at: null,
  created_at: "2026-09-26T08:00:00Z",
  ...overrides,
});

function clientWithBatches() {
  const client = testQueryClient();
  client.setQueryData(allBatchesQuery("active").queryKey, {
    items: [
      {
        id: "b1",
        name: "CSE 2026",
        description: "",
        status: "active",
        member_count: 3,
        created_at: "",
        updated_at: "",
      },
    ],
    truncated: false,
  });
  return client;
}

const csv = (name = "students.csv", body = "email,full_name\na@college.test,A\n") =>
  new File([body], name, { type: "text/csv" });

describe("<UploadCard />", () => {
  it("rejects a non-CSV file before uploading", async () => {
    const upload = vi.fn();
    renderWithQuery(<UploadCard upload={upload} onStarted={vi.fn()} />, clientWithBatches());

    await userEvent.upload(screen.getByLabelText("CSV file"), csv("students.xlsx"), {
      applyAccept: false,
    });
    await userEvent.click(screen.getByRole("button", { name: "Start import" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("must be a .csv file");
    expect(upload).not.toHaveBeenCalled();
  });

  it("uploads with the chosen batch, shows progress, and reports the job", async () => {
    let finish: (value: ImportJob) => void = () => undefined;
    const upload = vi.fn(
      (_file: File, _batch: string | null, onProgress: (f: number) => void) =>
        new Promise<ImportJob>((resolve) => {
          onProgress(0.5);
          finish = resolve;
        }),
    );
    const onStarted = vi.fn();
    renderWithQuery(
      <UploadCard initialBatchId="b1" upload={upload} onStarted={onStarted} />,
      clientWithBatches(),
    );

    expect(screen.getByLabelText("Add to batch")).toHaveValue("b1");
    await userEvent.upload(screen.getByLabelText("CSV file"), csv());
    await userEvent.click(screen.getByRole("button", { name: "Start import" }));

    expect(await screen.findByText("Uploading… 50%")).toBeInTheDocument();
    expect(upload).toHaveBeenCalledWith(expect.any(File), "b1", expect.any(Function));
    finish(job({ id: "job-42", status: "queued" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("job-42"));
    expect(screen.queryByTestId("upload-progress")).toBeNull();
  });

  it("shows API errors with their reference", async () => {
    const upload = vi.fn().mockRejectedValue(
      new ApiError({
        status: 429,
        code: "rate_limited",
        message: "Too many requests.",
        requestId: "req-9",
      }),
    );
    renderWithQuery(<UploadCard upload={upload} onStarted={vi.fn()} />, clientWithBatches());
    await userEvent.upload(screen.getByLabelText("CSV file"), csv());
    await userEvent.click(screen.getByRole("button", { name: "Start import" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Too many requests.");
    expect(alert).toHaveTextContent("req-9");
  });
});

describe("<JobSummary />", () => {
  it("shows live progress while running", () => {
    renderWithQuery(<JobSummary job={job()} live />);
    expect(screen.getByTestId("import-status")).toHaveTextContent("Importing");
    expect(screen.getByTestId("import-processed")).toHaveTextContent("20/50");
    expect(screen.getByLabelText("Import progress")).toBeInTheDocument();
  });

  it("offers the error report when rows failed", () => {
    renderWithQuery(
      <JobSummary
        job={job({ status: "completed_with_errors", processed_rows: 50, error_count: 2 })}
      />,
    );
    expect(screen.getByTestId("import-status")).toHaveTextContent("Done, with errors");
    expect(screen.getByRole("link", { name: "Download errors (CSV)" })).toHaveAttribute(
      "href",
      "/backend/api/v1/imports/job-1/errors.csv",
    );
  });
});

describe("form schemas", () => {
  it("validates import files", () => {
    expect(importFileSchema.safeParse(csv()).success).toBe(true);
    expect(importFileSchema.safeParse(csv("x.csv", "")).success).toBe(false);
    const big = new File([new Uint8Array(MAX_IMPORT_BYTES + 1)], "big.csv");
    expect(importFileSchema.safeParse(big).success).toBe(false);
  });

  it("validates batches and invitations", () => {
    expect(batchFormSchema.safeParse({ name: "  ", description: "" }).success).toBe(false);
    expect(batchFormSchema.safeParse({ name: "CSE 2026", description: "" }).success).toBe(true);
    expect(
      inviteSchema.safeParse({ email: "nope", full_name: "", roles: ["student"], batch_ids: [] })
        .success,
    ).toBe(false);
    expect(
      inviteSchema.safeParse({ email: "a@college.test", full_name: "", roles: [], batch_ids: [] })
        .success,
    ).toBe(false);
  });
});
