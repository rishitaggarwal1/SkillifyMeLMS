"use client";

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { NativeSelect } from "@/components/native-select";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import type { ImportJob } from "@/lib/api/types";

import {
  FINISHED_IMPORT_STATUSES,
  allBatchesQuery,
  importErrorsUrl,
  importQuery,
  importsQuery,
} from "./api";
import { ErrorAlert, LoadMore, PageTitle, errorMessage } from "./ui";
import { importFileSchema, uploadImport } from "./upload";

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Importing",
  succeeded: "Done",
  completed_with_errors: "Done, with errors",
  failed: "Failed",
};

type Uploader = typeof uploadImport;

export function ImportsPage({
  initialBatchId,
  upload = uploadImport,
}: {
  initialBatchId?: string;
  upload?: Uploader;
}) {
  const [jobId, setJobId] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-4">
      <PageTitle title="Import students" />
      <UploadCard initialBatchId={initialBatchId} upload={upload} onStarted={setJobId} />
      {jobId ? <JobProgress jobId={jobId} /> : null}
      <ImportHistory />
    </div>
  );
}

export function UploadCard({
  initialBatchId,
  upload,
  onStarted,
}: {
  initialBatchId?: string;
  upload: Uploader;
  onStarted: (jobId: string) => void;
}) {
  const queryClient = useQueryClient();
  const batches = useQuery(allBatchesQuery("active"));
  const batchOptions = batches.data?.items ?? [];
  const [file, setFile] = useState<File | null>(null);
  const [batchId, setBatchId] = useState(initialBatchId ?? "");
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const parsed = importFileSchema.safeParse(file);
    if (!parsed.success) {
      setError(new Error(parsed.error.issues[0]?.message ?? "Invalid file."));
      return;
    }
    setProgress(0);
    try {
      const job = await upload(parsed.data, batchId || null, setProgress);
      onStarted(job.id);
      setFile(null);
      (event.target as HTMLFormElement).reset();
      void queryClient.invalidateQueries({ queryKey: ["imports"] });
    } catch (e) {
      setError(e);
    } finally {
      setProgress(null);
    }
  }

  const uploading = progress !== null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Upload a CSV</CardTitle>
        <CardDescription>
          One student per row with <code>email</code> and <code>full_name</code> columns (up to 5
          MB). New students get an email to set their password.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-4" noValidate>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="import-file">CSV file</Label>
            <Input
              id="import-file"
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={uploading}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="import-batch">Add to batch</Label>
            <NativeSelect
              id="import-batch"
              value={batchId}
              onChange={(e) => setBatchId(e.target.value)}
              disabled={uploading}
            >
              <option value="">No batch (organization only)</option>
              {batchOptions.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </NativeSelect>
            {batches.data?.truncated ? (
              <p className="text-xs text-muted-foreground">Showing the first 2,000 batches.</p>
            ) : null}
          </div>
          {uploading ? (
            <div className="flex flex-col gap-1" data-testid="upload-progress">
              <Progress value={Math.round(progress * 100)} aria-label="Upload progress" />
              <span className="text-xs text-muted-foreground tabular-nums">
                Uploading… {Math.round(progress * 100)}%
              </span>
            </div>
          ) : null}
          {error ? <ErrorAlert error={error} /> : null}
          <Button type="submit" disabled={uploading} className="self-start">
            {uploading ? "Uploading…" : "Start import"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

export function JobProgress({ jobId }: { jobId: string }) {
  const { data: job, error } = useQuery(importQuery(jobId));
  if (error) return <ErrorAlert error={error} />;
  if (!job) return null;
  return <JobSummary job={job} live />;
}

export function JobSummary({ job, live = false }: { job: ImportJob; live?: boolean }) {
  const finished = FINISHED_IMPORT_STATUSES.has(job.status);
  const percent = job.total_rows > 0 ? Math.round((job.processed_rows / job.total_rows) * 100) : 0;
  return (
    <Card data-testid={live ? "import-job" : undefined} aria-live={live ? "polite" : undefined}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <span className="truncate">{job.file_name}</span>
          <Badge
            variant={job.status === "failed" ? "destructive" : finished ? "default" : "secondary"}
            data-testid="import-status"
          >
            {STATUS_LABELS[job.status] ?? job.status}
          </Badge>
        </CardTitle>
        <CardDescription>{new Date(job.created_at).toLocaleString()}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {!finished ? <Progress value={percent} aria-label="Import progress" /> : null}
        <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          <Stat
            label="Rows"
            value={`${job.processed_rows}/${job.total_rows}`}
            testId="import-processed"
          />
          <Stat label="Added" value={job.created_count} testId="import-created" />
          <Stat label="Already there" value={job.skipped_count} testId="import-skipped" />
          <Stat label="Errors" value={job.error_count} testId="import-errors" />
        </dl>
        {job.error_message ? (
          <p role="alert" className="text-sm text-destructive">
            {job.error_message}
          </p>
        ) : null}
        {job.error_count > 0 ? (
          <a
            href={importErrorsUrl(job.id)}
            className={buttonVariants({ variant: "outline", size: "sm" })}
            download
          >
            Download errors (CSV)
          </a>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, testId }: { label: string; value: number | string; testId: string }) {
  return (
    <div className="rounded-md bg-muted/50 p-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums" data-testid={testId}>
        {value}
      </dd>
    </div>
  );
}

function ImportHistory() {
  const query = useInfiniteQuery(importsQuery());
  const jobs = query.data?.pages.flatMap((p) => p.items) ?? [];
  if (query.isSuccess && jobs.length === 0) return null;
  return (
    <section className="flex flex-col gap-2" aria-labelledby="import-history">
      <h2 id="import-history" className="text-base font-medium">
        Recent imports
      </h2>
      {query.error ? <ErrorAlert error={query.error} /> : null}
      {jobs.map((job) => (
        <JobSummary key={job.id} job={job} />
      ))}
      <LoadMore
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
    </section>
  );
}

export { errorMessage };
